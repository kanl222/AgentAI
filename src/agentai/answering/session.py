"""Evidence and document scope owned by exactly one answer request."""
from dataclasses import dataclass, field
import re
from typing import NotRequired, TypedDict

from ..access import require
from ..errors import ApplicationError
from ..settings import RetrievalSettings
from .evidence import merge_evidence, pack_evidence
from ..knowledge.text import tokenize
from ..knowledge.scoping import document_key, explicit_targets


class Evidence(TypedDict):
    id: str
    source: str
    text: str
    citation: str
    ref: NotRequired[str]
    document_id: NotRequired[str]
    version: NotRequired[str]
    validity: NotRequired[str]
    note: NotRequired[str]
    extraction_method: NotRequired[str]


@dataclass
class AnswerSession:
    question: str
    user: dict
    store: object
    history: list = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict, init=False)
    references: dict[str, str] = field(default_factory=dict, init=False)
    rows: list[Evidence] = field(default_factory=list, init=False)
    focus: list[str] = field(default_factory=list, init=False)
    anchors: list[str] = field(default_factory=list, init=False)
    pinned_documents: set = field(default_factory=set, init=False)
    scope_ready: bool = field(default=False, init=False)
    query: str = field(init=False)
    context_query: str = field(default='', init=False)
    equivalences: dict = field(default_factory=dict, init=False)
    routing_status: str = field(default='not_needed', init=False)

    def __post_init__(self):
        self.query = self.question[:2000]
        # Carry a document only for a reference to the preceding exchange.
        # An ordinary standalone question must search the whole authorized index.
        followup = bool(re.search(
            r'\b(?:при этом|об этом|в этом|для этого|из этого|'
            r'такой|такая|(?<!что )такое|такие|таким|таком|этот|эта|это|эти|'
            r'предыдущ\w*|вышеуказан\w*|упомянут\w*)\b', self.question.lower())
            or re.fullmatch(r'\s*(?:расскажи |объясни |покажи )?(?:подробнее|продолжи|продолжай)'
                            r'[\s.!?]*', self.question.lower()))
        if re.search(r'\b(?:другая тема|сменим тему|новый вопрос|теперь о другом)\b', self.question.lower()):
            followup = False
        if followup and self.history and not any(explicit_targets(self.question)):
            previous = self.history[-1]
            self.pinned_documents.update(tuple(key) for key in previous.get('context', {}).get('selected', []))
            targets = ' '.join(key[1].rsplit('/', 1)[-1].split('@', 1)[0]
                               for key in sorted(self.pinned_documents))[:400]
            if self.pinned_documents:
                self.context_query = (self.question[:1000] + ' ' + previous['question'][:500] + ' ' + targets)[:2000]

    def remember(self, rows):
        result = []
        for row in rows:
            reference = self.references.setdefault(row['id'], f'S{len(self.references)+1}')
            entry = dict(row, ref=reference)
            self.evidence[row['id']] = entry
            result.append(entry)
        return result

    @require('reader')
    def search(self, query):
        rows = self.store.search(query[:2000], self.user['sources'])
        if self.scope_ready:
            rows = [row for row in rows if document_key(row) in self.pinned_documents]
        rows = self.remember(rows)
        if rows:
            self.query = query[:2000]
            self.rows = merge_evidence(rows, self.rows)
            self.focus = []
            self.anchors = []
        return rows

    @require('reader')
    def read_source(self, chunk_id, offset=-1, limit=3):
        if chunk_id not in self.evidence:
            return None
        rows = self.remember(self.store.read_source(chunk_id, self.user['sources'], offset, max(1, min(6, limit))))
        if rows:
            self.rows = merge_evidence(rows, self.rows)
            self.focus = [row['id'] for row in rows]
        return rows

    def expand_neighbors(self):
        """Expand at most three hit files into ordered batches of six passages."""
        if not hasattr(self.store, 'read_source'):
            return
        expanded, seen = [], set()
        self.anchors = []
        for anchor in self.rows:
            key = document_key(anchor)
            if key in seen:
                continue
            seen.add(key)
            self.anchors.append(anchor['id'])
            neighbors = self.store.read_source(anchor['id'], self.user['sources'], -1, 6)
            # Do not join different files or document versions into one batch.
            neighbors = [row for row in neighbors if document_key(row) == key
                         and row.get('version') == anchor.get('version')
                         and row.get('document_id') == anchor.get('document_id')]
            expanded.extend(self.remember(neighbors or [anchor]))
            if len(seen) == 3:
                break
        groups = {}
        for row in merge_evidence(expanded, self.rows):
            groups.setdefault(document_key(row), []).append(row)
        self.rows = [row for group in groups.values() for row in sorted(group, key=lambda row:
                     [int(n) if n.isdigit() else n for n in re.split(r'(\d+)', row['citation'])])]

    def retrieve(self):
        try:
            self.search(self.query)
            if self.context_query:
                # Conversation context adds evidence; it never excludes a fresh
                # global search or changes the current user's search question.
                contextual = self.remember(self.store.search(self.context_query, self.user['sources']))
                self.rows = merge_evidence(contextual, self.rows)
            if any(explicit_targets(self.question)):
                self.pinned_documents.update(document_key(row) for row in self.rows)
                self.scope_ready = True
        except Exception:
            raise ApplicationError(503, 'Document search unavailable; check Qdrant and embeddings') from None

    def packed(self, budget=None):
        entries = pack_evidence(self.rows, budget=budget if budget is not None else
                                RetrievalSettings.from_env().context_chars, focus=self.focus, anchors=self.anchors)
        for entry in entries:
            # Source coordinates are useful, immutable storage addresses are not
            # model-authored links. The backend resolves refs to exact versions.
            if 'citation' in entry:
                entry['location'] = re.sub(r'@[^?#/]+(?=[?#]|$)', '', entry.pop('citation'))
            entry.pop('version', None)
        return entries

    def missing_target(self):
        files, identifiers = explicit_targets(self.question)
        if not self.rows and (files or identifiers):
            return 'Документ или символ ' + ', '.join(files or identifiers) + ' не найден среди доступных источников. Уточните название или загрузите документ.'
        return ''

    def direct_quote(self):
        phrase = tokenize(self.question)
        titled = [row for row in self.rows if row['citation'].startswith('document:')
                  and 3 <= len(phrase) <= 16
                  and any(tokenize(line) == phrase for line in row['text'].splitlines()[:30])]
        if not titled:
            return ''
        selected = [row for row in self.rows if any(
            row['id'] == hit['id'] or (row.get('document_id') and row.get('document_id') == hit.get('document_id')
            and row.get('version') == hit.get('version') and row['source'] == hit['source']) for hit in titled)]
        self.evidence = {row['id']: row for row in selected}
        return 'Найден документ. Фрагмент исходного текста:\n\n' + '\n\n'.join(
            '\n'.join('> ' + line for line in row['text'].splitlines()) + '\n\nИсточник: ' + row['citation']
            for row in selected)

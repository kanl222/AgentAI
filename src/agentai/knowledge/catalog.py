"""Bounded, source-derived descriptions for semantic document selection."""
import re

from .scoping import document_key
from .text import lexical_tokens


def describe_documents(rows, query, limit=48):
    groups = {}
    for row in rows:
        groups.setdefault(document_key(row), []).append(row)
    question = set(lexical_tokens(query)) - {'_'}

    def coverage(text):
        return len(question.intersection(lexical_tokens(text)))

    descriptions = []
    for key, entries in sorted(groups.items()):
        entries.sort(key=lambda row: [int(n) if n.isdigit() else n
                                     for n in re.split(r'(\d+)', row['citation'])])
        lines = [line.strip() for line in entries[0]['text'].splitlines()
                 if len(re.findall(r'[^\W\d_]', line)) >= 3]
        heading = ' '.join(lines[:3])[:220]
        candidates = [line.strip() for row in entries for line in row['text'].splitlines() if line.strip()]
        best = max(candidates, key=coverage, default='')
        # Descriptions select candidates; only original chunks support answers.
        description = re.sub(r'https?://\S+', '', heading+' … '+best[:340])
        descriptions.append({'key': key, 'description': description,
                             'coverage': max((coverage(row['text']) for row in entries), default=0)})
    if len(descriptions) > limit:
        descriptions.sort(key=lambda item: item['coverage'], reverse=True)
    return [dict(n=i, key=item['key'], description=item['description'])
            for i, item in enumerate(descriptions[:limit], 1)]

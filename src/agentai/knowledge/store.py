"""Hybrid retrieval and staged index publication."""
import json
import os
import re
from threading import RLock
from qdrant_client import QdrantClient, models
from .scoping import scope_records
from .heads import IndexHeads
from .text import tokenize, heading_in_question
from .cache import RetrievalCache
from .publication import publish_generation
from ..settings import RetrievalSettings

class Store:
    def __init__(self, embeddings, client=None, index_heads=None, collection=None):
        self.embeddings = embeddings
        self.client = client or QdrantClient(url=os.getenv('QDRANT_URL','http://qdrant:6333'))
        self.lock = RLock()
        self.collection = collection or os.getenv('QDRANT_COLLECTION', 'agentai')
        self.heads = index_heads or IndexHeads()
        self.settings = RetrievalSettings.from_env()
        self.cache = RetrievalCache(self.settings.corpus_entries, self.settings.corpus_ttl)

    @staticmethod
    def acl(sources):
        return models.Filter(must=[models.FieldCondition(key='source',match=models.MatchAny(any=list(sources)))])

    def replace(self, source, rows, document_id=None, progress=None):
        with self.lock:
            count = publish_generation(source, rows, embeddings=self.embeddings, client=self.client,
                                       heads=self.heads, collection=self.collection,
                                       document_id=document_id, progress=progress)
            self.cache.clear()
        return count

    def remove(self, source, document_id, version=None):
        with self.lock:
            self.heads.activate(source,document_id,None,removed_version=version)
            self.cache.clear()

    def corpus(self, sources):
        if not sources or not self.client.collection_exists(self.collection): return []
        with self.lock:
            heads, metadata = self.heads.view(sources)
            key = (tuple(sorted(set(sources))), tuple(sorted(
                (source,scope,generation) for (source,scope),generation in heads.items() if source in sources)))

            def load():
                return self._points(self.acl(sources), heads)

            points = self.cache.snapshot(key, load)
        return self._metadata(points, sources, metadata)

    def _points(self, query_filter, heads):
        points, offset = [], None
        while True:
            batch, offset = self.client.scroll(self.collection, scroll_filter=query_filter,
                                              limit=256, offset=offset, with_payload=True, with_vectors=False)
            points.extend(point for point in batch if self.heads.visible(point.payload, heads))
            if offset is None:
                return points

    def _metadata(self, points, sources, info=None):
        if info is None and self.heads.database:
            with self.heads.database.connect() as db:
                info = {r['version']:r for r in db.execute('SELECT version,validity,note FROM versions WHERE source=ANY(%s)', (list(sources),)).fetchall()}
        for point in points:
            item = (info or {}).get(point.payload.get('version'))
            if item: point.payload.update(validity=item['validity'],note=item['note'])
        return points

    def visible_ids(self, identities, sources):
        if not identities or not sources or not self.client.collection_exists(self.collection):
            return set()
        query_filter = self.acl(sources)
        query_filter.must.append(models.HasIdCondition(has_id=list(identities)))
        return {point.payload['id'] for point in self._points(query_filter, self.heads.snapshot())}

    def read_source(self, row_id, sources, offset=0, limit=3):
        from ..knowledge.scoping import document_key
        if not sources or not self.client.collection_exists(self.collection):
            return []
        heads = self.heads.snapshot()
        query_filter = self.acl(sources)
        query_filter.must.append(models.HasIdCondition(has_id=[row_id]))
        found = self._points(query_filter, heads)
        target = found[0].payload if found else None
        if not target: return []
        if target.get('document_id') and target.get('version'):
            query_filter = self.acl([target['source']])
            query_filter.must.extend(models.FieldCondition(key=key, match=models.MatchValue(value=target[key]))
                                     for key in ('document_id', 'version'))
            points = self._metadata(self._points(query_filter, heads), sources)
        else:
            # Existing code/legacy chunks lack version metadata; keep their original grouping.
            points = self.corpus(sources)
        same = [p.payload for p in points if document_key(p.payload)==document_key(target)]
        same.sort(key=lambda row:[int(n) if n.isdigit() else n for n in re.split(r'(\d+)',row['citation'])])
        index = next((i for i,row in enumerate(same) if row['id']==row_id), None)
        if index is None:
            return []
        start = max(0,index+max(-2,min(100,offset)))
        return same[start:start+max(1,min(6,limit))]

    def document_catalog(self, query, sources):
        from .catalog import describe_documents
        return describe_documents([point.payload for point in self.corpus(sources)], query)

    def search(self, query, sources, *, documents=None):
        original_query = query
        aliases = json.loads(os.getenv('SEARCH_ALIASES','{}'))
        additions = [term for key,values in aliases.items() if key.lower() in query.lower() for term in values if isinstance(term,str)]
        query = (query+' '+' '.join(additions))[:2000]
        if not sources or not self.client.collection_exists(self.collection):
            return []
        with self.lock:
            corpus = self.corpus(sources)
            if not corpus:
                return []
            scoped, targeted = scope_records([p.payload for p in corpus], original_query)
            allowed_ids = {row['id'] for row in scoped}
            corpus = [p for p in corpus if p.payload['id'] in allowed_ids]
            if documents is not None:
                from .scoping import document_key
                keys = {tuple(key) for key in documents}
                corpus = [p for p in corpus if document_key(p.payload) in keys]
                allowed_ids = {p.payload['id'] for p in corpus}
            if not corpus:
                return []
            active_filter = self.acl(sources)
            active_filter.must.append(models.HasIdCondition(has_id=[p.id for p in corpus]))
            vector = self.cache.embedding(self.embeddings, query, self.settings.embedding_entries, self.settings.embedding_ttl)
            candidates = max(self.settings.candidates, self.settings.top_k)
            dense = self.client.query_points(self.collection,query=vector,query_filter=active_filter,limit=candidates).points
            dense = [p for p in dense if p.payload['id'] in allowed_ids]
            scores = self.cache.scores(corpus, query)
            lexical = [corpus[i] for i in sorted(range(len(corpus)),key=lambda i:scores[i],reverse=True)[:candidates] if scores[i]>0]
            fused, records = {}, {}
            canonical = {p.id:p.payload for p in corpus}
            if targeted:
                # Include all chunks of the explicit target even outside dense top 20.
                for point in corpus:
                    fused[point.id] = 1
                    records[point.id] = point.payload
            for ranking in (dense,lexical):
                for rank,p in enumerate(ranking,1):
                    fused[p.id] = fused.get(p.id,0)+1/(60+rank)
                    records[p.id] = canonical[p.id]
            # Short exact phrases such as a document title must survive dense noise.
            query_terms = tokenize(query)
            if 3 <= len(query_terms) <= 16:
                phrase = ' '.join(query_terms)
                for point in corpus:
                    if (phrase in ' '.join(tokenize(point.payload['text']))
                            or heading_in_question(point.payload['text'], original_query)):
                        fused[point.id] = fused.get(point.id,0)+1
                        records[point.id] = point.payload
            return [records[k] for k in sorted(fused,key=fused.get,reverse=True)[:self.settings.top_k]]

"""Bounded caches; a different ACL or active generation never reuses a corpus."""
from collections import OrderedDict
from copy import deepcopy
from time import monotonic

from rank_bm25 import BM25Okapi

from .text import lexical_tokens


class RetrievalCache:
    def __init__(self, limit=4, ttl=30):
        self.limit, self.ttl = limit, ttl
        self.corpora = OrderedDict()
        self.rankers = OrderedDict()
        self.query_vectors = OrderedDict()
        self.embedding_owner = None

    def embedding(self, owner, query, limit, ttl):
        if owner is not self.embedding_owner:
            self.query_vectors.clear()
            self.embedding_owner = owner
        if not limit or not ttl:
            return owner.embed_query(query)
        cached = self.query_vectors.get(query)
        if cached is None or monotonic()-cached[0] >= ttl:
            # Cache only successful calls. Index visibility is independently checked on every search.
            cached = (monotonic(), tuple(owner.embed_query(query)))
            self.query_vectors[query] = cached
        self.query_vectors.move_to_end(query)
        while len(self.query_vectors) > limit:
            self.query_vectors.popitem(last=False)
        return list(cached[1])

    def snapshot(self, key, load):
        if not self.limit or not self.ttl:
            return load()
        cached = self.corpora.get(key)
        if cached is None or monotonic()-cached[0] >= self.ttl:
            cached = (monotonic(), load())
            self.corpora[key] = cached
        self.corpora.move_to_end(key)
        while len(self.corpora) > self.limit:
            self.corpora.popitem(last=False)
        # Metadata hydration and callers must not mutate the shared snapshot.
        return deepcopy(cached[1])

    def scores(self, points, query):
        if not self.limit:
            return BM25Okapi([lexical_tokens(point.payload['text']) for point in points]).get_scores(lexical_tokens(query))
        key = tuple((point.id, point.payload['text']) for point in points)
        if key not in self.rankers:
            self.rankers[key] = BM25Okapi([lexical_tokens(point.payload['text']) for point in points])
        self.rankers.move_to_end(key)
        while len(self.rankers) > self.limit:
            self.rankers.popitem(last=False)
        return self.rankers[key].get_scores(lexical_tokens(query))

    def clear(self):
        self.corpora.clear()
        self.rankers.clear()

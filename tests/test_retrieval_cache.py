import pytest

from agentai.knowledge.cache import RetrievalCache
from agentai.settings import RetrievalSettings


class Embeddings:
    def __init__(self): self.calls = 0
    def embed_query(self, query):
        self.calls += 1
        return [float(self.calls), 0., 1.]


def test_query_vectors_are_bounded_expire_and_cannot_be_mutated(monkeypatch):
    import agentai.knowledge.cache as module
    now = [100.]
    monkeypatch.setattr(module, 'monotonic', lambda: now[0])
    cache, model = RetrievalCache(), Embeddings()
    first = cache.embedding(model, 'one', 2, 30)
    first[0] = 99
    assert cache.embedding(model, 'one', 2, 30)[0] == 1
    assert model.calls == 1
    cache.embedding(model, 'two', 2, 30)
    cache.embedding(model, 'three', 2, 30)
    assert len(cache.query_vectors) == 2
    cache.embedding(model, 'one', 2, 30)
    assert model.calls == 4
    now[0] += 31
    cache.embedding(model, 'one', 2, 30)
    assert model.calls == 5


def test_query_vector_cache_is_invalidated_when_model_changes_and_can_be_disabled():
    cache, first, second = RetrievalCache(), Embeddings(), Embeddings()
    cache.embedding(first, 'same', 4, 30)
    cache.embedding(second, 'same', 4, 30)
    assert second.calls == 1
    cache.embedding(second, 'same', 0, 30)
    cache.embedding(second, 'same', 4, 0)
    assert second.calls == 3


def test_failed_embedding_is_never_cached():
    class Unavailable(Embeddings):
        def embed_query(self, query): raise OSError('offline')
    cache = RetrievalCache()
    with pytest.raises(OSError):
        cache.embedding(Unavailable(), 'query', 4, 30)
    assert not cache.query_vectors


def test_cache_settings_keep_configuration_bounded(monkeypatch):
    monkeypatch.setenv('QUERY_EMBED_CACHE_ENTRIES', '99999')
    monkeypatch.setenv('RETRIEVAL_CACHE_TTL_SECONDS', '-1')
    settings = RetrievalSettings.from_env()
    assert settings.embedding_entries == 512 and settings.corpus_ttl == 0

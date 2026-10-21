from copy import deepcopy
import uuid

import pytest
from qdrant_client import QdrantClient, models
from agentai.knowledge.heads import IndexHeads
from agentai.knowledge.migration import reembed_collection, scroll_all, payload_digest


class Embeddings:
    def embed_documents(self, texts):
        return [[1., .5] for text in texts]


def source():
    client = QdrantClient(':memory:')
    client.create_collection('old', vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    heads = IndexHeads(in_memory=True)
    heads.activate('public', 'doc', 'active')
    rows = [{'source': 'public', 'document_id': 'doc', '_scope': 'doc', '_generation': generation,
             'text': generation, 'citation': 'original.pdf?page=1'} for generation in ('active', 'staging')]
    client.upsert('old', points=[models.PointStruct(id=str(uuid.uuid4()), vector=[1., 0., 0.], payload=row) for row in rows])
    return client, heads


def test_migration_preserves_ids_payload_visibility_and_old_vectors():
    client, heads = source()
    before = payload_digest(scroll_all(client, 'old'))
    original_heads = deepcopy(heads.snapshot())
    result = reembed_collection(client, Embeddings(), heads, 'old', 'new')
    assert result['points'] == 1 and result['dimensions'] == 2 and result['source_unchanged']
    assert heads.snapshot() == original_heads
    assert payload_digest(scroll_all(client, 'old')) == before
    copied = scroll_all(client, 'new')
    active = [point for point in scroll_all(client, 'old') if point.payload['_generation'] == 'active']
    assert payload_digest(copied) == payload_digest(active)
    assert client.get_collection('old').config.params.vectors.size == 3
    assert client.get_collection('new').config.params.vectors.size == 2


def test_migration_rejects_existing_target_without_overwrite():
    client, heads = source()
    before = payload_digest(scroll_all(client, 'old'))
    with pytest.raises(ValueError, match='new collection'):
        reembed_collection(client, Embeddings(), heads, 'old', 'old')
    assert payload_digest(scroll_all(client, 'old')) == before


def test_changed_source_is_detected_before_switch():
    client, heads = source()
    def changed(current, total):
        heads.activate('public', 'doc', 'another')
    with pytest.raises(ValueError, match='Source changed'):
        reembed_collection(client, Embeddings(), heads, 'old', 'new', changed)


def test_incomplete_embeddings_do_not_modify_source():
    client, heads = source()
    class Broken:
        def embed_documents(self, texts):
            return []
    before = payload_digest(scroll_all(client, 'old'))
    with pytest.raises(ValueError, match='Incomplete'):
        reembed_collection(client, Broken(), heads, 'old', 'new')
    assert not client.collection_exists('new')
    assert payload_digest(scroll_all(client, 'old')) == before

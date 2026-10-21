"""Re-embed visible points into a new collection without changing visibility."""
import hashlib
import json
import math

from qdrant_client import models


def payload_digest(points):
    value = sorted([(str(point.id), point.payload) for point in points], key=lambda row: row[0])
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scroll_all(client, collection):
    points, offset = [], None
    while True:
        batch, offset = client.scroll(collection, limit=256, offset=offset, with_payload=True, with_vectors=False)
        points.extend(batch)
        if offset is None:
            return points


def reembed_collection(client, embeddings, heads, source, target, progress=None):
    """Caller must pause writes through cutover; no source or head is modified."""
    if source == target or client.collection_exists(target):
        raise ValueError('Target must be a new collection distinct from the source')
    snapshot = heads.snapshot()
    original = scroll_all(client, source)
    source_digest = payload_digest(original)
    points = [point for point in original if heads.visible(point.payload, snapshot)]
    if not points:
        raise ValueError('Source has no visible points')
    dimensions = None
    for start in range(0, len(points), 8):
        batch = points[start:start+8]
        vectors = embeddings.embed_documents([point.payload['text'] for point in batch])
        if len(vectors) != len(batch) or any(not vector or not all(math.isfinite(value) for value in vector) for vector in vectors):
            raise ValueError('Incomplete or invalid embeddings; source remains unchanged')
        dimensions = dimensions or len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise ValueError('Embedding dimensions changed during migration')
        if start == 0:
            client.create_collection(target, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))
        client.upsert(target, points=[models.PointStruct(id=point.id, payload=point.payload, vector=vector)
                                      for point, vector in zip(batch, vectors)], wait=True)
        if progress:
            progress(min(start+len(batch), len(points)), len(points))
    if heads.snapshot() != snapshot or payload_digest(scroll_all(client, source)) != source_digest:
        raise ValueError('Source changed during migration; do not switch readers')
    copied = scroll_all(client, target)
    if payload_digest(copied) != payload_digest(points):
        raise ValueError('Target payload verification failed; do not switch readers')
    return {'source': source, 'target': target, 'points': len(points),
            'documents': len({point.payload.get('document_id') for point in points}),
            'dimensions': dimensions, 'payload_sha256': payload_digest(points), 'source_unchanged': True}

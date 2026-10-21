"""Publish only complete generations; PostgreSQL owns the visibility switch."""
import uuid

from qdrant_client import models


def publish_generation(source, rows, *, embeddings, client, heads, collection, document_id=None, progress=None):
    rows = list(rows)
    if not rows:
        raise ValueError('No text; scanned PDFs require OCR')
    generation = str(uuid.uuid4())
    scope = document_id or '*'
    for start in range(0, len(rows), 16):
        batch = [dict(row) for row in rows[start:start+16]]
        vectors = embeddings.embed_documents([row['text'] for row in batch])
        if len(vectors) != len(batch):
            raise ValueError('Incomplete embedding batch')
        if not client.collection_exists(collection):
            client.create_collection(collection, vectors_config=models.VectorParams(
                size=len(vectors[0]), distance=models.Distance.COSINE))
        for row in batch:
            row.update(_scope=scope, _generation=generation)
            row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, generation+row['id']))
        client.upsert(collection, points=[models.PointStruct(id=row['id'], vector=vector, payload=row)
                                         for row, vector in zip(batch, vectors)], wait=True)
        if progress:
            progress(10+int(80*min(start+16, len(rows))/len(rows)))
    heads.activate(source, scope, generation, rows[0].get('version'))
    return len(rows)

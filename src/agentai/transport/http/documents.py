"""HTTP routes for documents."""
import json
import os
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from ...commands import Upload, Sync, DocumentMetadata
from ...access import require
from ...execution.queue import Jobs
from ...adapters.gitlab import gitlab


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.post('/ingest/document')
    @require('indexer', source=lambda body, **kw: body.source)
    def ingest_document(body: Upload, p=Depends(user)):
        if not application.capacity.acquire(blocking=False):
            raise HTTPException(429, 'One active operation allowed')
        try:
            return application.index_version(application.save_upload(body, p), p)
        finally:
            application.capacity.release()

    @router.get('/documents')
    @require('reader')
    def list_documents(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), p=Depends(user)):
        return {'versions': application.archive().list(p['sources'], limit, offset)}

    @router.get('/documents/{version}/original')
    @require('reader')
    def download_document(version: uuid.UUID, p=Depends(user)):
        row = application.archive().get(str(version), p['sources'])
        if row is None or not application.archive().path(str(version)).is_file():
            raise HTTPException(404, 'Document not found')
        return FileResponse(application.archive().path(str(version)), filename=row['filename'], media_type='application/octet-stream',
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.post('/ingest/gitlab')
    @require('indexer', source=lambda body, **kw: body.source)
    def ingest_gitlab(body: Sync, p=Depends(user)):
        if body.source not in json.loads(os.getenv('GITLAB_SOURCES', '{}')):
            raise HTTPException(404, 'Source not configured')
        if not application.capacity.acquire(blocking=False):
            raise HTTPException(429, 'One active operation allowed')
        try:
            return {'chunks': application.services()[0].replace(body.source, gitlab(body.source))}
        finally:
            application.capacity.release()

    @router.post('/documents/{version}/reindex', status_code=202)
    def reindex_document(version: uuid.UUID, p=Depends(user)):
        item = application.archive().get(str(version), p['sources'])
        if not item:
            raise HTTPException(404, 'Document not found')
        require(p, 'indexer', item['source'])
        return Jobs(application.database()).enqueue('index', {'version': str(version)}, p, item['source'])

    @router.patch('/documents/{version}')
    def update_document(version: uuid.UUID, body: DocumentMetadata, p=Depends(user)):
        item = application.archive().get(str(version), p['sources'])
        if not item:
            raise HTTPException(404, 'Document not found')
        require(p, 'indexer', item['source'])
        application.archive().metadata(str(version), body.validity, body.note)
        return {'updated': True}

    @router.delete('/documents/{version}', status_code=202)
    def remove_document(version: uuid.UUID, p=Depends(user)):
        item = application.archive().get(str(version), p['sources'])
        if not item:
            raise HTTPException(404, 'Document not found')
        require(p, 'indexer', item['source'])
        return Jobs(application.database()).enqueue('remove', {'version': str(version)}, p, item['source'])

    @router.get('/sources/{chunk_id}')
    @require('reader')
    def read_source(chunk_id: uuid.UUID, offset: int = Query(default=-1, ge=-2, le=100), limit: int = Query(default=3, ge=1, le=6), p=Depends(user)):
        rows = application.services()[0].read_source(str(chunk_id), p['sources'], offset, limit)
        if not rows:
            raise HTTPException(404, 'Source not found')
        return {'chunks': rows}

    return router

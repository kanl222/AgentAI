"""HTTP routes for jobs."""
import json
import os
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from ...commands import Chat, Upload, Sync
from ...access import require
from ...jobs import Jobs


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.post('/jobs/chat', status_code=202)
    def queue_chat(body: Chat, p=Depends(user)):
        return application.queue_chat(body, p)

    @router.post('/jobs/documents', status_code=202)
    def queue_document(body: Upload, p=Depends(user)):
        version = application.save_upload(body, p)
        try:
            job = Jobs(application.database()).enqueue('index', {'version': version}, p, body.source)
        except Exception:
            application.archive().finish(version, False)
            raise
        return dict(job, version=version)

    @router.post('/jobs/gitlab', status_code=202)
    @require('indexer', source=lambda body, **kw: body.source)
    def queue_gitlab(body: Sync, p=Depends(user)):
        if body.source not in json.loads(os.getenv('GITLAB_SOURCES', '{}')):
            raise HTTPException(404, 'Source not configured')
        return Jobs(application.database()).enqueue('gitlab', {'source': body.source}, p, body.source)

    @router.get('/jobs')
    def list_jobs(p=Depends(user)):
        return {'jobs': Jobs(application.database()).list(p)}

    @router.get('/jobs/{identity}')
    def get_job(identity: uuid.UUID, p=Depends(user)):
        return Jobs(application.database()).get(str(identity), p)

    @router.delete('/jobs/{identity}')
    def cancel_job(identity: uuid.UUID, p=Depends(user)):
        Jobs(application.database()).cancel(str(identity), p)
        return {'cancelled': True}

    return router

"""HTTP assembly, lifespan and translation of application failures."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ...errors import ApplicationError
from ...execution.queue import Jobs
from ...execution.worker import Worker
from . import chat, documents, jobs, openai, system


def create_app(application):
    @asynccontextmanager
    async def lifespan(app):
        worker = None
        if os.getenv('DATABASE_URL'):
            application.database()
            if os.getenv('ENABLE_JOB_WORKER', 'true').lower() == 'true':
                worker = Worker(Jobs(application.database()), application.capacity, application.dispatch_job)
                worker.start()
        try:
            yield
        finally:
            if worker:
                worker.close()

    app = FastAPI(title='Local AI Agent', version='0.2.0', lifespan=lifespan)
    app.state.application = application

    @app.exception_handler(ApplicationError)
    async def application_error(request, error):
        return JSONResponse(status_code=error.status_code, content={'detail': error.detail})

    @app.middleware('http')
    async def body_limit(request: Request, call_next):
        if request.method == 'POST':
            data = bytearray()
            async for piece in request.stream():
                data.extend(piece)
                if len(data) > 28_000_000:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
            request._body = bytes(data)
        return await call_next(request)

    for module in (system, chat, documents, jobs, openai):
        app.include_router(module.create_router(application))
    return app

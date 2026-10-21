"""ASGI entry point and compatibility hooks for existing integrations."""
from .access import authenticate
from .answering.engine import answer
from .adapters.lmstudio import lmstudio_clients
from .bootstrap import database, archive, services
from .commands import Chat, Upload, Sync, Completion, DocumentMetadata
from .adapters.embeddings import request_embeddings
from .application import Application
from .execution.capacity import Capacity
from .transport.http.app import create_app as _create_app

capacity = Capacity()


def application():
    # Late binding preserves existing integration overrides without coupling modules to api.
    return Application(services=lambda: services(), database=lambda: database(),
                       archive=lambda: archive(), answer=lambda *a, **kw: answer(*a, **kw),
                       authenticate=lambda value: authenticate(value),
                       request_embeddings=lambda body: request_embeddings(body), capacity=capacity)


def create_app(runtime=None):
    return _create_app(runtime or application())


def run_chat(body, p):
    return application().run_chat(body, p)


def dispatch_job(job, p, progress):
    return application().dispatch_job(job, p, progress)


app = create_app()

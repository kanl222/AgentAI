"""Own external dependencies; constructing HTTP routes performs no I/O."""
import os
from functools import lru_cache
from .adapters.lmstudio import lmstudio_clients
from .adapters.postgres import Database
from .adapters.archive import DocumentArchive
from .errors import ApplicationError
from .knowledge.store import Store


@lru_cache
def database():
    if not os.getenv('DATABASE_URL'):
        raise ApplicationError(503, 'Configure DATABASE_URL for pilot features')
    result = Database()
    result.migrate()
    return result


@lru_cache
def archive():
    return DocumentArchive(os.getenv('DOCUMENTS_DIR', 'data/documents'),
                           database=database() if os.getenv('DATABASE_URL') else None)


@lru_cache
def services():
    embeddings, model = lmstudio_clients()
    return Store(embeddings), model

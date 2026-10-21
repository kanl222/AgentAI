"""Operations shared by HTTP handlers and durable background jobs."""
import os
import uuid
from dataclasses import dataclass
from typing import Callable

from .access import require
from .chat.service import run_chat
from .chat.history import Conversations
from .execution.dispatch import dispatch_job
from .ingestion.service import save_upload, index_version
from .execution.queue import Jobs
from .settings import bounded_int
from .errors import ApplicationError


@dataclass
class Application:
    services: Callable
    database: Callable
    archive: Callable
    answer: Callable
    authenticate: Callable
    request_embeddings: Callable
    capacity: object

    def chat(self, body, user):
        require(user, 'reader')
        if not self.capacity.acquire(blocking=False):
            raise ApplicationError(429, 'One active operation allowed')
        try:
            return self.run_chat(body, user)
        finally:
            self.capacity.release()

    def run_chat(self, body, user, job_id=None):
        memory = Conversations(self.database()) if os.getenv('DATABASE_URL') else None
        return run_chat(body, user, services=self.services, memory=memory,
                        answer=self.answer, max_steps=bounded_int('MAX_STEPS', 4, 1, 8), job_id=job_id)

    def save_upload(self, body, user):
        return save_upload(body, user, archive=self.archive)

    def index_version(self, version, user, progress=None):
        return index_version(version, user, progress, archive=self.archive, services=self.services)

    def queue_chat(self, body, user):
        require(user, 'reader')
        memory = Conversations(self.database())
        if body.conversation_id:
            memory.get(str(body.conversation_id), user)
        else:
            body.conversation_id = uuid.UUID(memory.create(user))
        return Jobs(self.database()).enqueue('chat', body.model_dump(mode='json'), user)

    def dispatch_job(self, job, user, progress):
        return dispatch_job(job, user, progress, application=self)

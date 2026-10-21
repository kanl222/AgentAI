"""HTTP routes for chat."""
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from ...commands import Chat
from ...access import require
from ...chat.history import Conversations


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.post('/chat')
    @require('reader')
    def chat(body: Chat, p=Depends(user)):
        return application.chat(body, p)

    @router.post('/conversations')
    @require('reader')
    def create_conversation(p=Depends(user)):
        return {'id': Conversations(application.database()).create(p)}

    @router.get('/conversations')
    @require('reader')
    def list_conversations(p=Depends(user)):
        return {'conversations': Conversations(application.database()).list(p)}

    @router.get('/conversations/{identity}')
    @require('reader')
    def get_conversation(identity: uuid.UUID, p=Depends(user)):
        return Conversations(application.database()).get(str(identity), p, display=True)

    @router.delete('/conversations/{identity}')
    @require('reader')
    def delete_conversation(identity: uuid.UUID, p=Depends(user)):
        Conversations(application.database()).delete(str(identity), p)
        return {'deleted': True}

    return router

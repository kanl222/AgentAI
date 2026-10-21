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
    def chat(body: Chat,p=Depends(user)):
        return application.chat(body,p)


    @router.post('/conversations')
    def create_conversation(p=Depends(user)):
        require(p,'reader')
        return {'id':Conversations(application.database()).create(p)}


    @router.get('/conversations')
    def list_conversations(p=Depends(user)):
        require(p,'reader')
        return {'conversations':Conversations(application.database()).list(p)}


    @router.get('/conversations/{identity}')
    def get_conversation(identity: uuid.UUID, p=Depends(user)):
        require(p,'reader')
        return Conversations(application.database()).get(str(identity),p,display=True)


    @router.delete('/conversations/{identity}')
    def delete_conversation(identity: uuid.UUID, p=Depends(user)):
        require(p,'reader')
        Conversations(application.database()).delete(str(identity),p)
        return {'deleted':True}

    return router

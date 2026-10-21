"""HTTP routes for openai."""
import os
import time
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from ...commands import Chat, Completion
from ...access import require
from ...adapters.postgres import principal
from ...execution.queue import Jobs
from ...adapters.embeddings import EmbeddingRequest


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.get('/v1/models')
    def models(p=Depends(user)):
        require(p,'reader')
        return {'object':'list','data':[{'id':'local-agent','object':'model','owned_by':'local'}]}


    @router.post('/v1/embeddings')
    def embeddings(body: EmbeddingRequest, p=Depends(user)):
        require(p, 'reader')
        if not application.capacity.acquire(blocking=False):
            raise HTTPException(429, 'One active operation allowed')
        try:
            return application.request_embeddings(body)
        finally:
            application.capacity.release()


    @router.post('/v1/chat/completions')
    def completion(body: Completion,p=Depends(user), webui_chat_id: str | None = Header(default=None,alias='X-OpenWebUI-Chat-Id',max_length=200)):
        if body.stream:
            raise HTTPException(400,'Disable streaming for this MVP')
        questions = [str(m.get('content','')) for m in body.messages if m.get('role')=='user']
        if not questions:
            raise HTTPException(422,'User question required')
        identity = body.conversation_id
        external_id = body.chat_id or webui_chat_id
        if not identity and external_id and os.getenv('DATABASE_URL'):
            # External chat identifiers are scoped by the authenticated backend principal.
            identity = uuid.uuid5(uuid.NAMESPACE_URL,principal(p)+':'+external_id)
            with application.database().connect() as db:
                db.execute("INSERT INTO conversations(id,owner,title) VALUES (%s,%s,'Диалог WebUI') ON CONFLICT(id) DO NOTHING", (identity,principal(p)))
        question = Chat(question=questions[-1],conversation_id=identity)
        if os.getenv('DATABASE_URL') and os.getenv('QUEUE_WEBUI_REQUESTS','true').lower()=='true':
            queued = application.queue_chat(question,p)
            timeout = max(30,min(600,int(os.getenv('CHAT_QUEUE_WAIT_SECONDS','240'))))
            deadline = time.monotonic()+timeout
            while True:
                job = Jobs(application.database()).get(queued['id'],p)
                if job['state']=='succeeded':
                    result = job['result']
                    break
                if job['state'] in {'failed','cancelled'}:
                    raise HTTPException(503,{'message':'Chat job failed','job_id':queued['id']})
                if time.monotonic()>=deadline:
                    raise HTTPException(504,{'message':'Job continues in queue; inspect its status','job_id':queued['id']})
                time.sleep(0.5)
        else:
            result = application.chat(question,p)
        return {'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
                'model':'local-agent','conversation_id':result.get('conversation_id'),
                'choices':[{'index':0,'message':{'role':'assistant','content':result['answer']},'finish_reason':result.get('finish_reason','stop')}]}

    return router

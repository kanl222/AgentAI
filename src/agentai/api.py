import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from functools import lru_cache
from threading import BoundedSemaphore
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from .core import authenticate, require, Store, document, gitlab
from .agent import answer
from .documents import DocumentArchive
from .embeddings import EmbeddingRequest, request_embeddings
from .database import Database, principal
from .conversations import Conversations
from .jobs import Jobs, Worker


@lru_cache
def database():
    if not os.getenv('DATABASE_URL'):
        raise HTTPException(503,'Configure DATABASE_URL for pilot features')
    return Database()


@asynccontextmanager
async def lifespan(app):
    worker = None
    if os.getenv('DATABASE_URL') and os.getenv('ENABLE_JOB_WORKER','true').lower()=='true':
        worker = Worker(Jobs(database()),capacity,dispatch_job)
        worker.start()
    yield
    if worker: worker.close()

app = FastAPI(title='Local AI Agent',version='0.2.0',lifespan=lifespan)
capacity = BoundedSemaphore(1)


@app.middleware('http')
async def body_limit(request: Request, call_next):
    from starlette.responses import JSONResponse
    # Count actual streamed bytes, including requests without Content-Length.
    if request.method == 'POST':
        data = bytearray()
        async for piece in request.stream():
            data.extend(piece)
            if len(data)>28_000_000:
                return JSONResponse({'detail':'Request too large'},status_code=413)
        request._body = bytes(data)
    return await call_next(request)


def user(authorization: str | None = Header(default=None)):
    return authenticate(authorization)


@lru_cache
def services():
    embeddings, model = lmstudio_clients()
    store = Store(embeddings)
    return store,model


def lmstudio_clients(http_client=None):
    options = dict(base_url=os.getenv('LMSTUDIO_BASE_URL','http://host.docker.internal:1234/v1').rstrip('/'),
                   api_key=os.getenv('LMSTUDIO_API_KEY','lm-studio'),
                   timeout=180,max_retries=0)
    if http_client is not None:
        options['http_client'] = http_client
    embeddings = OpenAIEmbeddings(model=os.getenv('EMBED_MODEL','text-embedding-nomic-embed-text-v1.5'),
                                  check_embedding_ctx_length=False,chunk_size=16,**options)
    model = ChatOpenAI(model=os.getenv('CHAT_MODEL','qwen3-4b'),temperature=0,
                       max_tokens=max(256,min(4096,int(os.getenv('CHAT_MAX_TOKENS','2400')))),**options)
    return embeddings,model


@app.get('/health')
def health():
    return {'status':'alive'}


@app.get('/ready')
def ready(p=Depends(user)):
    import httpx
    require(p,'reader')
    try:
        with httpx.Client(timeout=5) as client:
            headers={'Authorization':'Bearer '+os.getenv('LMSTUDIO_API_KEY','lm-studio')}
            response=client.get(os.getenv('LMSTUDIO_BASE_URL','http://host.docker.internal:1234/v1').rstrip('/')+'/models',headers=headers)
            response.raise_for_status()
            if os.getenv('DATABASE_URL'):
                with database().connect() as db: db.execute('SELECT 1')
            names={m['id'] for m in response.json()['data']}
            wanted={os.getenv('CHAT_MODEL','qwen3-4b'),os.getenv('EMBED_MODEL','text-embedding-nomic-embed-text-v1.5')}
            if not wanted <= names:
                raise ValueError('Models missing')
            response=client.get(os.getenv('QDRANT_URL','http://qdrant:6333')+'/readyz')
            response.raise_for_status()
    except Exception as error:
        raise HTTPException(503,'Dependencies unavailable; check models and services') from error
    return {'status':'ready'}


class Chat(BaseModel):
    question: str = Field(min_length=1,max_length=12000)
    conversation_id: uuid.UUID | None = None


def run_chat(body, p):
    require(p,'reader')
    store,model = services()
    memory = Conversations(database()) if os.getenv('DATABASE_URL') else None
    identity = str(body.conversation_id) if body.conversation_id else None
    history = []
    if memory:
        if identity:
            history = memory.get(identity,p)['turns']
            active_ids = {point.payload['id'] for point in store.corpus(p['sources'])}
            history = [turn for turn in history if all(row['id'] in active_ids for row in turn['sources'])]
        else:
            identity = memory.create(p)
    options = {'max_steps':max(1,min(8,int(os.getenv('MAX_STEPS','4'))))}
    if history: options['history'] = history
    result = answer(body.question,p,store,model,**options)
    if memory:
        memory.append(identity,p,body.question,result)
        result['conversation_id'] = identity
    return result


@app.post('/chat')
def chat(body: Chat,p=Depends(user)):
    require(p,'reader')
    if not capacity.acquire(blocking=False):
        raise HTTPException(429,'One active operation allowed')
    try:
        return run_chat(body,p)
    finally:
        capacity.release()


class Upload(BaseModel):
    source: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    filename: str = Field(max_length=200)
    content_base64: str = Field(max_length=28_000_000)
    document_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')


@lru_cache
def archive():
    return DocumentArchive(os.getenv('DOCUMENTS_DIR', 'data/documents'))


def save_upload(body, p):
    require(p,'indexer',body.source)
    try:
        data = base64.b64decode(body.content_base64,validate=True)
        filename = body.filename.replace('\\','/').rsplit('/',1)[-1]
        if not filename or any(ord(c)<32 for c in filename): raise ValueError('Invalid filename')
        if Path(filename).suffix.lower() not in {'.pdf','.docx','.txt','.md'}: raise ValueError('Unsupported document type')
        if len(data)>20_000_000: raise ValueError('Maximum file size: 20 MB')
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    identity = body.document_id or uuid.uuid5(uuid.NAMESPACE_URL,body.source+'/'+filename).hex
    return archive().save(body.source,identity,filename,data,p['name'])


def index_version(version, p, progress=None):
    catalog = archive()
    item = catalog.get(version,p['sources'])
    if not item: raise HTTPException(404,'Document not found')
    require(p,'indexer',item['source'])
    try:
        rows = list(document(catalog.path(version).read_bytes(),item['filename'],item['source']))
        if not rows: raise ValueError('No extractable text; scanned PDFs require OCR')
        for row in rows:
            row.update(document_id=item['document_id'],version=version,download_url=f'/documents/{version}/original')
            row['citation'] = row['citation'].replace('document:'+item['source']+'/'+item['filename'],
                                                     'document:'+item['source']+'/'+item['filename']+'@'+version,1)
            row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL,version+row['id']))
        if catalog.database and any('не актуально' in row['text'].lower() for row in rows):
            catalog.metadata(version,'obsolete','В тексте есть отметка «Не актуально»')
        options = {'document_id':item['document_id']}
        if progress: options['progress'] = progress
        count = services()[0].replace(item['source'],rows,**options)
        if not catalog.database: catalog.finish(version,True)
        return {'chunks':count,'document_id':item['document_id'],'version':version,'download_url':f'/documents/{version}/original'}
    except Exception:
        catalog.finish(version,False)
        raise HTTPException(503,'Original saved, indexing failed; retry after checking dependencies') from None


@app.post('/ingest/document')
def ingest_document(body: Upload,p=Depends(user)):
    require(p,'indexer',body.source)
    if not capacity.acquire(blocking=False):
        raise HTTPException(429,'One active operation allowed')
    try:
        return index_version(save_upload(body,p),p)
    finally:
        capacity.release()


@app.get('/documents')
def list_documents(limit: int = Query(default=50,ge=1,le=200), offset: int = Query(default=0,ge=0), p=Depends(user)):
    require(p,'reader')
    return {'versions':archive().list(p['sources'],limit,offset)}


@app.get('/documents/{version}/original')
def download_document(version: uuid.UUID,p=Depends(user)):
    require(p,'reader')
    row = archive().get(str(version),p['sources'])
    if row is None or not archive().path(str(version)).is_file():
        raise HTTPException(404,'Document not found')
    return FileResponse(archive().path(str(version)),filename=row['filename'],media_type='application/octet-stream',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


class Sync(BaseModel):
    source: str


@app.post('/ingest/gitlab')
def ingest_gitlab(body: Sync,p=Depends(user)):
    require(p,'indexer',body.source)
    if body.source not in json.loads(os.getenv('GITLAB_SOURCES','{}')):
        raise HTTPException(404,'Source not configured')
    if not capacity.acquire(blocking=False):
        raise HTTPException(429,'One active operation allowed')
    try:
        return {'chunks':services()[0].replace(body.source,gitlab(body.source))}
    finally:
        capacity.release()


@app.get('/v1/models')
def models(p=Depends(user)):
    require(p,'reader')
    return {'object':'list','data':[{'id':'local-agent','object':'model','owned_by':'local'}]}


@app.post('/v1/embeddings')
def embeddings(body: EmbeddingRequest, p=Depends(user)):
    require(p, 'reader')
    if not capacity.acquire(blocking=False):
        raise HTTPException(429, 'One active operation allowed')
    try:
        return request_embeddings(body)
    finally:
        capacity.release()


class Completion(BaseModel):
    model: str = 'local-agent'
    messages: list[dict] = Field(max_length=40)
    stream: bool = False
    conversation_id: uuid.UUID | None = None
    chat_id: str | None = Field(default=None,max_length=200)


@app.post('/v1/chat/completions')
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
        with database().connect() as db:
            db.execute("INSERT INTO conversations(id,owner,title) VALUES (%s,%s,'Диалог WebUI') ON CONFLICT(id) DO NOTHING", (identity,principal(p)))
    question = Chat(question=questions[-1],conversation_id=identity)
    if os.getenv('DATABASE_URL') and os.getenv('QUEUE_WEBUI_REQUESTS','true').lower()=='true':
        queued = queue_chat(question,p)
        timeout = max(30,min(600,int(os.getenv('CHAT_QUEUE_WAIT_SECONDS','240'))))
        deadline = time.monotonic()+timeout
        while True:
            job = Jobs(database()).get(queued['id'],p)
            if job['state']=='succeeded':
                result = job['result']
                break
            if job['state'] in {'failed','cancelled'}:
                raise HTTPException(503,{'message':'Chat job failed','job_id':queued['id']})
            if time.monotonic()>=deadline:
                raise HTTPException(504,{'message':'Job continues in queue; inspect its status','job_id':queued['id']})
            time.sleep(0.5)
    else:
        result = chat(question,p)
    return {'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
            'model':'local-agent','conversation_id':result.get('conversation_id'),
            'choices':[{'index':0,'message':{'role':'assistant','content':result['answer']},'finish_reason':result.get('finish_reason','stop')}]}


@app.post('/conversations')
def create_conversation(p=Depends(user)):
    require(p,'reader')
    return {'id':Conversations(database()).create(p)}


@app.get('/conversations')
def list_conversations(p=Depends(user)):
    require(p,'reader')
    return {'conversations':Conversations(database()).list(p)}


@app.get('/conversations/{identity}')
def get_conversation(identity: uuid.UUID, p=Depends(user)):
    require(p,'reader')
    return Conversations(database()).get(str(identity),p)


@app.delete('/conversations/{identity}')
def delete_conversation(identity: uuid.UUID, p=Depends(user)):
    require(p,'reader')
    Conversations(database()).delete(str(identity),p)
    return {'deleted':True}


@app.post('/jobs/chat',status_code=202)
def queue_chat(body: Chat, p=Depends(user)):
    require(p,'reader')
    if body.conversation_id: Conversations(database()).get(str(body.conversation_id),p)
    else: body.conversation_id = uuid.UUID(Conversations(database()).create(p))
    return Jobs(database()).enqueue('chat',body.model_dump(mode='json'),p)


@app.post('/jobs/documents',status_code=202)
def queue_document(body: Upload, p=Depends(user)):
    version = save_upload(body,p)
    try:
        job = Jobs(database()).enqueue('index',{'version':version},p,body.source)
    except Exception:
        archive().finish(version,False)
        raise
    return dict(job,version=version)


@app.post('/jobs/gitlab',status_code=202)
def queue_gitlab(body: Sync, p=Depends(user)):
    require(p,'indexer',body.source)
    if body.source not in json.loads(os.getenv('GITLAB_SOURCES','{}')): raise HTTPException(404,'Source not configured')
    return Jobs(database()).enqueue('gitlab',{'source':body.source},p,body.source)


@app.get('/jobs')
def list_jobs(p=Depends(user)):
    return {'jobs':Jobs(database()).list(p)}


@app.get('/jobs/{identity}')
def get_job(identity: uuid.UUID, p=Depends(user)):
    return Jobs(database()).get(str(identity),p)


@app.delete('/jobs/{identity}')
def cancel_job(identity: uuid.UUID, p=Depends(user)):
    Jobs(database()).cancel(str(identity),p)
    return {'cancelled':True}


@app.post('/documents/{version}/reindex',status_code=202)
def reindex_document(version: uuid.UUID, p=Depends(user)):
    item = archive().get(str(version),p['sources'])
    if not item: raise HTTPException(404,'Document not found')
    require(p,'indexer',item['source'])
    return Jobs(database()).enqueue('index',{'version':str(version)},p,item['source'])


class DocumentMetadata(BaseModel):
    validity: str = Field(pattern='^(current|obsolete|draft)$')
    note: str = Field(default='',max_length=1000)


@app.patch('/documents/{version}')
def update_document(version: uuid.UUID, body: DocumentMetadata, p=Depends(user)):
    item = archive().get(str(version),p['sources'])
    if not item: raise HTTPException(404,'Document not found')
    require(p,'indexer',item['source'])
    archive().metadata(str(version),body.validity,body.note)
    return {'updated':True}


@app.delete('/documents/{version}',status_code=202)
def remove_document(version: uuid.UUID, p=Depends(user)):
    item = archive().get(str(version),p['sources'])
    if not item: raise HTTPException(404,'Document not found')
    require(p,'indexer',item['source'])
    return Jobs(database()).enqueue('remove',{'version':str(version)},p,item['source'])


@app.get('/sources/{chunk_id}')
def read_source(chunk_id: uuid.UUID, offset: int = Query(default=-1,ge=-2,le=100), limit: int = Query(default=3,ge=1,le=6), p=Depends(user)):
    require(p,'reader')
    rows = services()[0].read_source(str(chunk_id),p['sources'],offset,limit)
    if not rows: raise HTTPException(404,'Source not found')
    return {'chunks':rows}


def dispatch_job(job, p, progress):
    payload = job['payload']
    if job['kind']=='chat':
        result = run_chat(Chat(**payload),p)
        result['access_sources'] = p['sources']
        return result
    require(p,'indexer',job['source'])
    if job['kind']=='index': return index_version(payload['version'],p,progress)
    if job['kind']=='gitlab':
        return {'chunks':services()[0].replace(job['source'],gitlab(job['source']),progress=progress)}
    if job['kind']=='remove':
        item = archive().get(payload['version'],p['sources'])
        if not item: raise HTTPException(404,'Document not found')
        if item['state']!='indexed': raise HTTPException(409,'Only current indexed version can be removed')
        services()[0].remove(item['source'],item['document_id'])
        archive().remove_from_search(item['version'])
        return {'removed_from_search':True,'original_retained':True}
    raise ValueError('Unknown job kind')


@app.get('/library',include_in_schema=False)
def library():
    return FileResponse('web/library.html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'"})


@app.get('/favicon.ico',include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get('/assets/{name}',include_in_schema=False)
def asset(name: str):
    if name not in {'library.js','library.css'}: raise HTTPException(404)
    return FileResponse(Path('web')/name)

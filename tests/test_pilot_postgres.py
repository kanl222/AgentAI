import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from agentai.errors import ApplicationError as HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from qdrant_client import QdrantClient

from agentai import api
from agentai.conversations import Conversations
from agentai.core import Store, chunks
from agentai.database import Database
from agentai.jobs import Jobs, Worker
from test_required_retrieval import Embeddings

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='Requires isolated PostgreSQL TEST_DATABASE_URL')


def actor(name='alice', sources=None):
    return {'name':name,'sha256':hashlib.sha256(name.encode()).hexdigest(),'roles':['reader','indexer'],'sources':['a'] if sources is None else sources}


@pytest.fixture
def pilot(monkeypatch):
    users = [actor(),actor('bob'),actor('other',['b'])]
    monkeypatch.setenv('AGENT_USERS',json.dumps(users))
    store = Store(Embeddings(),QdrantClient(':memory:'))
    class Model:
        seen = []
        def bind_tools(self, tools): return self
        def invoke(self, messages):
            self.seen.append(messages)
            return AIMessage(content='Ответ по документу')
    model = Model()
    monkeypatch.setattr(api,'services',lambda:(store,model))
    return TestClient(api.app),store,model


def headers(name='alice'): return {'Authorization':'Bearer '+name}


def test_conversations_persist_and_isolate():
    user = actor()
    memory = Conversations(Database())
    identity = memory.create(user)
    memory.append(identity,user,'Первый вопрос',{'answer':'Первый ответ','sources':[]})
    reopened = Conversations(Database())
    assert reopened.get(identity,user)['turns'][0]['answer']=='Первый ответ'
    with pytest.raises(HTTPException): reopened.get(identity,actor('bob'))
    assert reopened.get(identity,actor(sources=[]))['turns']==[]
    reopened.delete(identity,user)
    with pytest.raises(HTTPException): memory.get(identity,user)


def test_retention_and_history_budget(monkeypatch):
    memory = Conversations(Database()); user=actor(); identity=memory.create(user)
    memory.append(identity,user,'x',{'answer':'a'*5000,'sources':[]})
    memory.append(identity,user,'y',{'answer':'b'*2000,'sources':[]})
    assert len(memory.get(identity,user)['turns'])==1
    with memory.database.connect() as db:
        db.execute('UPDATE conversations SET updated_at=%s WHERE id=%s', (datetime.now(timezone.utc)-timedelta(days=31),identity))
    assert memory.list(user)==[]


def test_display_history_is_complete_but_model_context_stays_bounded():
    memory=Conversations(Database()); user=actor(); identity=memory.create(user)
    memory.append(identity,user,'Первый вопрос',{'answer':'a'*5000,'sources':[]})
    memory.append(identity,user,'Второй вопрос',{'answer':'b'*2000,'sources':[]})
    assert len(memory.get(identity,user)['turns'])==1
    displayed=memory.get(identity,user,display=True)
    assert len(displayed['turns'])==2
    assert len(displayed['turns'][0]['answer'])==5000
    assert displayed['title']=='Первый вопрос'
    assert memory.get(identity,actor(sources=[]),display=True)['turns']==[]
    with pytest.raises(HTTPException): memory.get(identity,actor('bob'),display=True)


def test_pending_chat_exposes_identity_without_exposing_payload():
    memory=Conversations(Database()); jobs=Jobs(Database()); user=actor()
    identity=memory.create(user)
    queued=jobs.enqueue('chat',{'question':'Вопрос','conversation_id':identity},user)
    job=jobs.get(queued['id'],user)
    assert job['conversation_id']==identity
    assert 'payload' not in job
    with pytest.raises(HTTPException): jobs.get(queued['id'],actor('bob'))


def test_api_continuation_and_clear(pilot):
    client,store,model=pilot
    first=client.post('/chat',headers=headers(),json={'question':'Объясни метод'}).json()
    identity=first['conversation_id']
    result=client.post('/chat',headers=headers(),json={'question':'Продолжи','conversation_id':identity})
    assert result.status_code==200
    assert any(isinstance(m,HumanMessage) and m.content=='Объясни метод' for m in model.seen[-1])
    assert client.get('/conversations/'+identity,headers=headers('bob')).status_code==404
    assert client.delete('/conversations/'+identity,headers=headers()).status_code==200


def test_webui_external_chat_ids_are_owner_scoped(pilot):
    client,_,_=pilot
    body={'chat_id':'same-client-id','messages':[{'role':'user','content':'Первый'}]}
    a=client.post('/v1/chat/completions',headers=headers(),json=body).json()
    b=client.post('/v1/chat/completions',headers=headers('bob'),json=body).json()
    assert a['conversation_id']!=b['conversation_id']


def test_webui_header_continues_memory_without_trusting_user_headers(pilot):
    client,_,model=pilot
    auth=dict(headers(), **{'X-OpenWebUI-Chat-Id':'webui-dialog','X-OpenWebUI-User-Id':'forged'})
    a=client.post('/v1/chat/completions',headers=auth,json={'messages':[{'role':'user','content':'Первый вопрос'}]}).json()
    b=client.post('/v1/chat/completions',headers=auth,json={'messages':[{'role':'user','content':'Продолжи'}]}).json()
    assert a['conversation_id']==b['conversation_id']
    assert any(isinstance(m,HumanMessage) and m.content=='Первый вопрос' for m in model.seen[-1])
    assert client.get('/conversations/'+a['conversation_id'],headers=headers('bob')).status_code==404


def test_staged_index_failure_preserves_old_version(pilot):
    _,store,_=pilot
    old=list(chunks('Old good text','a','old.md'))
    store.replace('a',old,document_id='doc')
    class FailsSecondBatch(Embeddings):
        calls=0
        def embed_documents(self,texts):
            self.calls+=1
            if self.calls==2: raise RuntimeError('offline')
            return super().embed_documents(texts)
    store.embeddings=FailsSecondBatch()
    rows=[dict(old[0],id=str(i),text='Uncommitted '+str(i)) for i in range(20)]
    with pytest.raises(RuntimeError): store.replace('a',rows,document_id='doc')
    store.embeddings=Embeddings()
    assert [r['text'] for r in store.search('good',['a'])]==['Old good text']
    reopened=Store(Embeddings(),store.client)
    assert [r['text'] for r in reopened.search('good',['a'])]==['Old good text']


def test_neighbors_are_ordered_and_acl_filtered(pilot):
    _,store,_=pilot
    store.replace('a',chunks('\n'.join('Line '+str(i) for i in range(120)),'a','document:a/long.md'))
    rows=store.search('long.md',['a'])
    ordered=sorted(rows,key=lambda r:int(r['citation'].split('#L')[1].split('-')[0]))
    row=ordered[1]
    adjacent=store.read_source(row['id'],['a'],-1,3)
    assert len(adjacent)==3 and '#L1-' in adjacent[0]['citation']
    assert store.read_source(row['id'],['b'])==[]


def test_jobs_persist_cancel_and_enforce_owner(pilot,monkeypatch):
    jobs=Jobs(Database()); user=actor()
    job=jobs.enqueue('chat',{'question':'hello'},user)
    assert Jobs(Database()).get(job['id'],user)['state']=='queued'
    with pytest.raises(HTTPException): jobs.get(job['id'],actor('bob'))
    jobs.cancel(job['id'],user)
    assert jobs.get(job['id'],user)['state']=='cancelled'
    monkeypatch.setenv('MAX_PENDING_JOBS','1')
    jobs.enqueue('chat',{'question':'hello'},user)
    with pytest.raises(HTTPException) as e: jobs.enqueue('chat',{'question':'hello'},user)
    assert e.value.status_code==429


def test_worker_revalidates_permissions(pilot,monkeypatch):
    jobs=Jobs(Database()); user=actor()
    job=jobs.enqueue('chat',{'question':'hello'},user)
    monkeypatch.setenv('AGENT_USERS','[]')
    worker=Worker(jobs,api.capacity,api.dispatch_job)
    assert worker.process_one()
    assert jobs.get(job['id'],user)['state']=='failed'


def test_document_queue_management_and_revoked_memory(pilot):
    client,store,_=pilot
    response=client.post('/jobs/documents',headers=headers(),json={'source':'a','filename':'policy.md','content_base64':base64.b64encode(b'Policy text').decode()})
    assert response.status_code==202
    job=response.json(); version=job['version']
    jobs=Jobs(Database()); worker=Worker(jobs,api.capacity,api.dispatch_job)
    worker.process_one()
    assert jobs.get(job['id'],actor())['state']=='succeeded'
    assert store.search('policy',['a'])
    assert client.post('/documents/'+version+'/reindex',headers=headers('other')).status_code==404
    assert client.patch('/documents/'+version,headers=headers(),json={'validity':'obsolete','note':'Old policy'}).status_code==200
    assert store.search('policy',['a'])[0]['validity']=='obsolete'
    deleted=client.delete('/documents/'+version,headers=headers())
    assert deleted.status_code==202
    worker.process_one()
    assert store.search('policy',['a'])==[]
    assert client.get('/documents/'+version+'/original',headers=headers()).content==b'Policy text'
    assert client.post('/documents/'+version+'/reindex',headers=headers()).status_code==202
    worker.process_one()
    assert store.search('policy',['a'])


def test_library_is_static_and_does_not_embed_secrets(pilot):
    client,_,_=pilot
    response=client.get('/library')
    assert response.status_code==200 and 'script-src' in response.headers['content-security-policy']
    assert client.get('/assets/library.js').status_code==200
    assert client.get('/assets/secret.env').status_code==404


def test_deleting_conversation_also_deletes_chat_job_payload(pilot):
    client,_,_=pilot
    identity=client.post('/conversations',headers=headers()).json()['id']
    job=client.post('/jobs/chat',headers=headers(),json={'question':'private question','conversation_id':identity}).json()
    assert client.delete('/conversations/'+identity,headers=headers()).status_code==200
    assert client.get('/jobs/'+job['id'],headers=headers()).status_code==404


def test_failure_and_cancel_leave_original_available(pilot):
    client,_,_=pilot
    job=client.post('/jobs/documents',headers=headers(),json={'source':'a','filename':'bad.pdf','content_base64':base64.b64encode(b'invalid PDF').decode()}).json()
    worker=Worker(Jobs(Database()),api.capacity,api.dispatch_job)
    worker.process_one()
    assert client.get('/jobs/'+job['id'],headers=headers()).json()['state']=='failed'
    versions=client.get('/documents',headers=headers()).json()['versions']
    assert versions[0]['state']=='failed'
    assert client.get('/documents/'+job['version']+'/original',headers=headers()).content==b'invalid PDF'


def test_long_last_turn_retains_context():
    memory=Conversations(Database()); user=actor(); identity=memory.create(user)
    memory.append(identity,user,'question',{'answer':'a'*12000,'sources':[]})
    turns=memory.get(identity,user)['turns']
    assert turns and len(turns[0]['answer'])<6000


def test_russian_word_forms_boost_relevant_source(pilot):
    _,store,_=pilot
    for i in range(8):
        store.replace(str(i),chunks('Справочник подразделений содержит parent_id' if i==7 else 'Совершенно другой материал',str(i),str(i)))
    assert store.search('Где название подразделения?',[str(i) for i in range(8)])[0]['source']=='7'


def test_webui_completion_waits_for_queued_job(pilot,monkeypatch):
    import threading
    import time
    client,_,_=pilot
    monkeypatch.setenv('QUEUE_WEBUI_REQUESTS','true')
    worker=Worker(Jobs(Database()),api.capacity,api.dispatch_job)
    stop=threading.Event()
    def run():
        while not stop.is_set():
            if worker.process_one(): return
            time.sleep(0.01)
    thread=threading.Thread(target=run)
    thread.start()
    try:
        result=client.post('/v1/chat/completions',headers=headers(),json={'chat_id':'queued-webui','messages':[{'role':'user','content':'Вопрос'}]})
        assert result.status_code==200
        assert result.json()['conversation_id']
        assert Jobs(Database()).list(actor())[0]['state']=='succeeded'
    finally:
        stop.set(); thread.join(timeout=5)

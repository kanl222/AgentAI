import base64
import hashlib
import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from agentai import api
from agentai.core import Store


class Embeddings:
    def embed_documents(self,texts):return [[1.,0.,0.] for _ in texts]
    def embed_query(self,text):return [1.,0.,0.]


@pytest.fixture
def setup(monkeypatch):
    store=Store(Embeddings(),QdrantClient(':memory:'))
    monkeypatch.setattr(api,'services',lambda:(store,Mock()))
    users=[{'name':key,'sha256':hashlib.sha256(key.encode()).hexdigest(),'roles':roles,'sources':sources}
           for key,roles,sources in [('uploader',['reader','indexer'],['a']),('reader',['reader'],['a']),('outsider',['reader'],['b'])]]
    monkeypatch.setenv('AGENT_USERS',json.dumps(users))
    return TestClient(api.app),store


def headers(key):return {'Authorization':'Bearer '+key}


def upload(client,name,text,document_id=None):
    body={'source':'a','filename':name,'content_base64':base64.b64encode(text.encode()).decode()}
    if document_id:body['document_id']=document_id
    response=client.post('/ingest/document',json=body,headers=headers('uploader'))
    assert response.status_code==200,response.text
    return response.json()


def test_original_versions_and_multiple_documents(setup):
    client,store=setup
    first=upload(client,'policy.md','Old policy')
    other=upload(client,'manual.txt','Manual text')
    latest=upload(client,'policy.md','New policy')
    assert first['document_id']==latest['document_id']
    assert first['version']!=latest['version']
    rows=store.search('policy manual',['a'])
    assert {row['text'] for row in rows}=={'New policy','Manual text'}
    assert all(row['version'] in row['citation'] for row in rows)
    assert client.get(first['download_url'],headers=headers('reader')).content==b'Old policy'
    assert client.get(latest['download_url'],headers=headers('reader')).content==b'New policy'
    versions=client.get('/documents',headers=headers('reader')).json()['versions']
    assert len(versions)==3
    assert {row['version']:row['state'] for row in versions}[first['version']]=='superseded'
    assert len(client.get('/documents?limit=1&offset=1',headers=headers('reader')).json()['versions'])==1


def test_document_access_isolation(setup):
    client,_=setup
    record=upload(client,'../policy.md','Restricted policy')
    assert client.get(record['download_url']).status_code==401
    assert client.get(record['download_url'],headers=headers('outsider')).status_code==404
    assert client.get('/documents',headers=headers('outsider')).json()['versions']==[]
    assert client.get('/documents/not-a-uuid/original',headers=headers('reader')).status_code==422
    rows=client.get('/documents',headers=headers('reader')).json()['versions']
    assert rows[0]['filename']=='policy.md'


def test_failed_index_preserves_original(setup,monkeypatch):
    client,store=setup
    def fail(*args,**kwargs):raise RuntimeError('offline')
    monkeypatch.setattr(store,'replace',fail)
    response=client.post('/ingest/document',headers=headers('uploader'),json={
        'source':'a','filename':'test.md','content_base64':base64.b64encode(b'Keep original').decode()})
    assert response.status_code==503
    row=client.get('/documents',headers=headers('reader')).json()['versions'][0]
    assert row['state']=='failed'
    assert client.get('/documents/'+row['version']+'/original',headers=headers('reader')).content==b'Keep original'


def test_archive_survives_reopen(setup,isolated_document_archive):
    from agentai.documents import DocumentArchive
    client,_=setup
    result=upload(client,'policy.md','Persisted text')
    reopened=DocumentArchive(isolated_document_archive.root)
    row=reopened.get(result['version'],['a'])
    assert row['size']==len(b'Persisted text')
    assert reopened.path(row['version']).read_bytes()==b'Persisted text'

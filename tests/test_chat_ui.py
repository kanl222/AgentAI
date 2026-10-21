import hashlib
import json

from fastapi.testclient import TestClient
from agentai import api


def test_api_console_is_public_but_session_requires_reader(monkeypatch):
    monkeypatch.setenv('AGENT_USERS', json.dumps([
        {'name':'reader','sha256':hashlib.sha256(b'private-key').hexdigest(),
         'roles':['reader'],'sources':['docs']},
        {'name':'writer','sha256':hashlib.sha256(b'writer-key').hexdigest(),
         'roles':['indexer'],'sources':['docs']},
    ]))
    client=TestClient(api.app)
    page=client.get('/')
    assert page.status_code==200
    assert 'консоль API' in page.text
    assert 'private-key' not in page.text
    assert "frame-ancestors 'none'" in page.headers['content-security-policy']
    assert client.get('/session').status_code==401
    assert client.get('/session',headers={'Authorization':'Bearer writer-key'}).status_code==403
    response=client.get('/session',headers={'Authorization':'Bearer private-key'})
    assert response.json()=={'name':'reader','roles':['reader'],'sources':['docs']}
    assert client.get('/assets/library.js').status_code==200
    assert client.get('/app.js').status_code==200
    assert client.get('/app.css').status_code==200
    assert client.get('/assets/chat.js').status_code==404
    assert client.get('/assets/not-allowed.js').status_code==404

import base64
import hashlib
import json
import struct

import httpx
import pytest
from fastapi.testclient import TestClient

from agentai import api
from agentai.embeddings import request_embeddings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('AGENT_USERS', json.dumps([
        {'name': role, 'sha256': hashlib.sha256(role.encode()).hexdigest(),
         'roles': [role], 'sources': []} for role in ['reader', 'indexer']
    ]))
    monkeypatch.setenv('EMBED_MODEL', 'test-embed')
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://lmstudio.test/v1')
    monkeypatch.setenv('LMSTUDIO_API_KEY', 'upstream-private-token')

    def handler(request):
        assert request.url == 'http://lmstudio.test/v1/embeddings'
        assert request.headers['Authorization'] == 'Bearer upstream-private-token'
        payload = json.loads(request.content)
        assert payload['model'] == 'test-embed'
        assert payload['encoding_format'] == 'float'
        return httpx.Response(200, json={
            'data': [{'index': i, 'embedding': [float(i), 0.5]} for i in reversed(range(len(payload['input'])))],
            'usage': {'prompt_tokens': 7, 'total_tokens': 7},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(api, 'request_embeddings', lambda body: request_embeddings(body, upstream))
        with TestClient(api.app) as test_client:
            yield test_client


def post(client, body, key='reader'):
    return client.post('/v1/embeddings', json=body, headers={'Authorization': 'Bearer ' + key})


@pytest.mark.parametrize('value', ['Пример документа', ['Первый', 'Второй']])
def test_embeddings_string_and_batch(client, value):
    result = post(client, {'input': value, 'model': 'test-embed'})
    assert result.status_code == 200
    body = result.json()
    assert body['object'] == 'list' and body['model'] == 'test-embed'
    assert body['usage'] == {'prompt_tokens': 7, 'total_tokens': 7}
    count = 1 if isinstance(value, str) else len(value)
    assert [row['index'] for row in body['data']] == list(range(count))
    assert body['data'][0]['embedding'] == [0.0, 0.5]


def test_base64_format(client):
    result = post(client, {'input': 'Example', 'encoding_format': 'base64'}).json()
    vector = base64.b64decode(result['data'][0]['embedding'])
    assert struct.unpack('<ff', vector) == (0.0, 0.5)


def test_roles_and_unknown_key(client):
    assert client.post('/v1/embeddings', json={'input': 'x'}).status_code == 401
    assert post(client, {'input': 'x'}, 'unknown').status_code == 401
    assert post(client, {'input': 'x'}, 'indexer').status_code == 403


@pytest.mark.parametrize('value', ['', '  ', [], ['ok', ''], [1, 2], 123, 'x' * 8193, ['x'] * 17, ['x' * 8192] * 5])
def test_input_limits(client, value):
    assert post(client, {'input': value}).status_code == 422


def test_model_and_unsupported_options(client):
    assert post(client, {'model': 'arbitrary-model', 'input': 'x'}).status_code == 400
    assert post(client, {'input': 'x', 'dimensions': 1}).status_code == 422


def test_busy(client):
    assert api.capacity.acquire(blocking=False)
    try:
        assert post(client, {'input': 'x'}).status_code == 429
    finally:
        api.capacity.release()
    assert post(client, {'input': 'x'}).status_code == 200


@pytest.mark.parametrize('failure', ['timeout', 'bad_response', 'unauthorized', 'indices', 'dimensions', 'rejected'])
def test_upstream_failures_are_safe_and_release_capacity(client, monkeypatch, failure):
    def handler(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('upstream-private-token', request=request)
        if failure == 'bad_response':
            return httpx.Response(200, text='upstream-private-token')
        if failure == 'unauthorized':
            return httpx.Response(401, text='upstream-private-token')
        if failure == 'rejected':
            return httpx.Response(400, text='private text')
        data = [{'index': 0, 'embedding': [0.1]}, {'index': 0 if failure == 'indices' else 1, 'embedding': [0.1, 0.2]}]
        return httpx.Response(200, json={'data': data, 'usage': {'prompt_tokens': 2, 'total_tokens': 2}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(api, 'request_embeddings', lambda body: request_embeddings(body, upstream))
        response = post(client, {'input': ['one', 'two']})
    assert response.status_code == (422 if failure == 'rejected' else 503)
    assert 'private' not in response.text
    assert api.capacity.acquire(blocking=False)
    api.capacity.release()

"""Service failures remain actionable, independent and free of secrets."""
import hashlib
import json
from unittest.mock import MagicMock

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from agentai import api
from agentai.adapters import service_checks as checks


@pytest.fixture
def services(monkeypatch):
    monkeypatch.delenv('CHAT_CONTEXT_LENGTH', raising=False)
    monkeypatch.delenv('EMBED_BASE_URL', raising=False)
    for key, value in {'DATABASE_URL': 'postgresql://secret:password@db/private',
                       'LMSTUDIO_BASE_URL': 'http://lm/v1', 'LMSTUDIO_API_KEY': 'secret-key',
                       'QDRANT_URL': 'http://qdrant', 'OCR_URL': 'http://ocr',
                       'CHAT_MODEL': 'chat', 'EMBED_MODEL': 'embed'}.items():
        monkeypatch.setenv(key, value)
    connect = MagicMock()
    connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
    monkeypatch.setattr(checks.psycopg, 'connect', connect)
    calls = []
    replies = {}

    def respond(request):
        calls.append(request)
        reply = replies.get(request.url.host)
        if callable(reply):
            return reply(request)
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            return reply
        return httpx.Response(200, json={'data': [{'id': 'chat'}, {'id': 'embed'}]})

    factory = httpx.Client
    monkeypatch.setattr(checks.httpx, 'Client', lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    return replies, calls, connect


def test_healthy_and_disabled_ocr(services, monkeypatch):
    replies, calls, connect = services
    monkeypatch.setenv('OCR_URL', '')
    result = checks.check_services()
    assert result['status'] == 'ready' and result['failed_services'] == []
    assert result['services']['ocr']['status'] == 'disabled'
    assert {r.url.host for r in calls} == {'lm', 'qdrant'}
    assert all(r.method == 'GET' for r in calls)
    assert next(r for r in calls if r.url.host == 'lm').headers['authorization'] == 'Bearer secret-key'
    assert all('authorization' not in r.headers for r in calls if r.url.host != 'lm')
    assert connect.call_args.kwargs['connect_timeout'] == 5
    assert 'statement_timeout=5000' in connect.call_args.kwargs['options']


def test_independent_failures_and_no_secret_leaks(services):
    replies, calls, connect = services
    replies['lm'] = httpx.ConnectError('http://secret:password@lm?key=secret-key')
    replies['ocr'] = httpx.Response(503, text='secret backend stacktrace')
    connect.side_effect = psycopg.OperationalError('postgresql://secret:password@db/private')
    result = checks.check_services()
    assert result['failed_services'] == ['postgres', 'lmstudio', 'ocr']
    assert result['services']['qdrant']['status'] == 'ok'
    assert result['services']['lmstudio']['code'] == 'connection_failed'
    assert result['services']['ocr']['http_status'] == 503
    assert len(calls) == 3
    assert not any(secret in json.dumps(result) for secret in ('password', 'secret-key', 'stacktrace', 'postgresql://'))


@pytest.mark.parametrize(('reply', 'code'), [
    (httpx.ReadTimeout('secret'), 'timeout'),
    (httpx.Response(401), 'http_error'),
    (httpx.Response(200, text='not json'), 'invalid_response'),
    (httpx.Response(200, json={'data': None}), 'invalid_response'),
    (httpx.Response(200, json={'data': [{'id': 'embed'}]}), 'models_missing'),
])
def test_lmstudio_failures(services, reply, code):
    services[0]['lm'] = reply
    result = checks.check_services()
    assert result['failed_services'] == ['lmstudio']
    assert result['services']['lmstudio']['code'] == code
    if code == 'models_missing':
        assert result['services']['lmstudio']['models']['chat']['available'] is False


@pytest.mark.parametrize(('error', 'code'), [
    (psycopg.errors.InvalidPassword('secret'), 'authentication_failed'),
    (psycopg.errors.InvalidCatalogName('secret'), 'database_missing'),
    (psycopg.errors.ConnectionTimeout('secret'), 'timeout'),
    (psycopg.errors.QueryCanceled('secret'), 'timeout'),
])
def test_database_errors(services, error, code):
    services[2].side_effect = error
    assert checks.check_services()['services']['postgres']['code'] == code


def test_missing_database_is_not_ready(services, monkeypatch):
    monkeypatch.delenv('DATABASE_URL')
    assert checks.check_services()['services']['postgres']['code'] == 'not_configured'
    services[2].assert_not_called()


@pytest.mark.parametrize(('actual', 'expected'), [(32768, 'ready'), (8192, 'unavailable'), (None, 'unavailable')])
def test_configured_context_is_checked_without_changing_model(services, monkeypatch, actual, expected):
    monkeypatch.setenv('CHAT_CONTEXT_LENGTH', '32768')
    def reply(request):
        if request.url.path == '/api/v1/models':
            instances = [] if actual is None else [{'id': 'chat', 'config': {'context_length': actual}}]
            return httpx.Response(200, json={'models': [{'key': 'chat', 'loaded_instances': instances}]})
        return httpx.Response(200, json={'data': [{'id': 'chat'}, {'id': 'embed'}]})
    services[0]['lm'] = reply
    result = checks.check_services()
    assert result['status'] == expected
    context = result['services']['lmstudio']['context']
    assert context['configured'] == 32768
    assert context['loaded'] == ([] if actual is None else [actual])
    assert all(request.method == 'GET' for request in services[1])
    if expected == 'unavailable':
        assert result['services']['lmstudio']['code'] == 'context_not_applied'


def test_invalid_context_configuration_is_actionable(services, monkeypatch):
    monkeypatch.setenv('CHAT_CONTEXT_LENGTH', '8192')
    monkeypatch.setenv('CHAT_MAX_TOKENS', '16384')
    result = checks.check_services()
    assert result['services']['lmstudio']['code'] == 'invalid_configuration'
    assert 'CHAT_MAX_TOKENS' in result['services']['lmstudio']['hint']


def test_dedicated_embedding_service_has_independent_readiness(services, monkeypatch):
    monkeypatch.setenv('EMBED_BASE_URL', 'http://cpu/v1')
    monkeypatch.setenv('EMBED_API_KEY', 'cpu-private')
    services[0]['lm'] = httpx.Response(200, json={'data': [{'id': 'chat'}]})
    report = checks.check_services()
    assert report['status'] == 'ready'
    assert set(report['services']['lmstudio']['models']) == {'chat'}
    assert report['services']['embeddings']['model'] == 'embed'
    assert next(r for r in services[1] if r.url.host == 'cpu').headers['authorization'] == 'Bearer cpu-private'
    services[0]['cpu'] = httpx.ConnectError('private endpoint')
    report = checks.check_services()
    assert report['failed_services'] == ['embeddings']
    assert report['services']['lmstudio']['status'] == 'ok'


def test_routes_require_reader_and_keep_failure_details(services, monkeypatch):
    users = [{'name': name, 'sha256': hashlib.sha256(name.encode()).hexdigest(), 'roles': roles, 'sources': []}
             for name, roles in [('reader', ['reader']), ('guest', [])]]
    monkeypatch.setenv('AGENT_USERS', json.dumps(users))
    client = TestClient(api.create_app())
    for path in ('/ready', '/diagnostics/services'):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer guest'}).status_code == 403
        response = client.get(path, headers={'Authorization': 'Bearer reader'})
        assert response.status_code == 200 and response.json()['status'] == 'ready'
        assert response.headers['cache-control'] == 'no-store'
    services[0]['qdrant'] = httpx.Response(503)
    response = client.get('/ready', headers={'Authorization': 'Bearer reader'})
    assert response.status_code == 503
    assert response.json()['failed_services'] == ['qdrant']
    assert response.json()['services']['lmstudio']['status'] == 'ok'
    assert client.get('/health').json() == {'status': 'alive'}

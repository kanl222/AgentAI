import ast
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from qdrant_client import QdrantClient

from agentai import api
from agentai.adapters.postgres import Database
from agentai.answering.session import AnswerSession
from agentai.errors import ApplicationError
from agentai.execution.capacity import Capacity
from agentai.indexing import IndexHeads
from agentai.ingestion.extract import chunks
from agentai.knowledge.store import Store
from test_required_retrieval import Embeddings


def test_application_instances_keep_models_and_evidence_separate(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)

    def runtime(source):
        store = Store(Embeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
        store.replace(source, chunks('Only '+source, source, source+'.md'))

        class Model:
            def bind_tools(self, tools): return self
            def invoke(self, messages): return AIMessage(content=source+' [S1]')

        return replace(api.application(), services=lambda: (store, Model()),
                       authenticate=lambda value: {'roles': ['reader'], 'sources': [source]}, capacity=Capacity())

    first, second = TestClient(api.create_app(runtime('first'))), TestClient(api.create_app(runtime('second')))
    for client, expected in ((first, 'first'), (second, 'second'), (first, 'first')):
        response = client.post('/chat', json={'question': 'Вопрос'})
        assert response.status_code == 200
        assert {row['source'] for row in response.json()['sources']} == {expected}


def test_core_operations_do_not_import_http_or_compatibility_entry_points():
    root = Path(__file__).resolve().parents[1]/'src/agentai'
    for folder in ('access', 'answering', 'chat', 'ingestion', 'knowledge', 'execution', 'adapters'):
        for path in (root/folder).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom):
                    assert node.module not in {'fastapi', 'starlette', 'api', 'core', 'agent'}, str(path)


def test_database_construction_does_not_connect_or_migrate(monkeypatch):
    def unexpected(*args, **kwargs): raise AssertionError('constructor must perform no I/O')
    monkeypatch.setattr(Database, 'connect', unexpected)
    assert Database('test-dsn').dsn == 'test-dsn'


def test_capacity_failure_does_not_poison_local_slot(monkeypatch):
    import agentai.execution.capacity as module
    monkeypatch.setenv('DATABASE_URL', 'test-dsn')
    capacity = Capacity()
    with monkeypatch.context() as unavailable:
        unavailable.setattr(module.psycopg, 'connect', lambda *a, **kw: (_ for _ in ()).throw(OSError('offline')))
        with pytest.raises(ApplicationError):
            capacity.acquire(blocking=False)
    monkeypatch.delenv('DATABASE_URL')
    assert capacity.acquire(blocking=False)
    capacity.release()


def test_evidence_is_owned_by_one_request_and_empty_search_keeps_it():
    class Search:
        def search(self, query, sources):
            return [dict(id='one', source='a', citation='a.md#L1', text='known')] if query=='first' else []

    first = AnswerSession('first', {'roles': ['reader'], 'sources': ['a']}, Search())
    second = AnswerSession('second', {'roles': ['reader'], 'sources': ['a']}, Search())
    first.search('first')
    first.search('empty')
    assert first.rows[0]['ref'] == 'S1'
    assert second.rows == [] and second.evidence == {}


def test_cached_search_keeps_acl_generations_and_caller_mutations_isolated(monkeypatch):
    store = Store(Embeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
    store.replace('a', chunks('old allowed text', 'a', 'allowed.md'), document_id='doc')
    store.replace('secret', chunks('private', 'secret', 'private.md'))
    calls = []
    original = store.client.scroll

    def scroll(*args, **kwargs):
        calls.append(kwargs.get('scroll_filter'))
        return original(*args, **kwargs)

    monkeypatch.setattr(store.client, 'scroll', scroll)
    first = store.search('allowed', ['a'])
    count = len(calls)
    first[0]['text'] = 'changed by caller'
    assert store.search('allowed', ['a'])[0]['text'] == 'old allowed text'
    assert len(calls) == count
    assert store.search('private', []) == []
    assert {row['source'] for row in store.search('private', ['secret'])} == {'secret'}
    old_id = store.search('allowed', ['a'])[0]['id']
    store.replace('a', chunks('new allowed text', 'a', 'allowed.md'), document_id='doc')
    assert store.search('allowed', ['a'])[0]['text'] == 'new allowed text'
    assert store.visible_ids([old_id], ['a']) == set()
    store.remove('a', 'doc')
    assert store.search('allowed', ['a']) == []


def test_version_neighbors_do_not_load_the_whole_corpus(monkeypatch):
    store = Store(Embeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
    rows = list(chunks('\n'.join('Line '+str(i) for i in range(120)), 'a', 'document:a/long.md'))
    for row in rows:
        row.update(document_id='doc', version='version')
    store.replace('a', rows, document_id='doc')
    row = store.search('long.md', ['a'])[1]
    monkeypatch.setattr(store, 'corpus', lambda *a: (_ for _ in ()).throw(AssertionError('full corpus load')))
    assert store.read_source(row['id'], ['a'], -1, 3)
    assert store.read_source(row['id'], ['secret'], -1, 3) == []

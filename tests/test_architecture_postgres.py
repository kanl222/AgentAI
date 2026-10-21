import os

import pytest

from agentai import api
from agentai.adapters.postgres import Database
from agentai.chat.history import Conversations
from agentai.errors import ApplicationError
from agentai.execution.capacity import Capacity
from agentai.execution.queue import Jobs
from agentai.execution.worker import Worker
from test_pilot_postgres import actor, headers, pilot

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL')


def test_gpu_capacity_is_shared_between_independent_instances():
    first, second = Capacity(), Capacity()
    assert first.acquire(blocking=False)
    try:
        assert not second.acquire(blocking=False)
    finally:
        first.release()
    assert second.acquire(blocking=False)
    second.release()


def test_migrations_can_be_repeated_without_losing_history():
    database = Database()
    memory = Conversations(database)
    user = actor()
    identity = memory.create(user)
    memory.append(identity, user, 'q', {'answer': 'saved', 'sources': []})
    database.migrate()
    database.migrate()
    assert memory.get(identity, user)['turns'][0]['answer'] == 'saved'
    with database.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM schema_versions').fetchone()['n'] == 1


def test_crash_after_chat_commit_keeps_one_turn_and_completed_job(pilot, monkeypatch):
    client, _, model = pilot
    queued = client.post('/jobs/chat', headers=headers(), json={'question': 'Вопрос'}).json()
    jobs = Jobs(Database())
    finish = jobs.finish
    calls = []

    def crash_once(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError('simulated crash after committed answer')
        return finish(*args, **kwargs)

    monkeypatch.setattr(jobs, 'finish', crash_once)
    worker = Worker(jobs, Capacity(), api.dispatch_job)
    assert worker.process_one()
    saved = jobs.get(queued['id'], actor())
    assert saved['state'] == 'succeeded'
    assert saved['result']['conversation_id'] == saved['conversation_id']
    assert len(Conversations(Database()).get(saved['conversation_id'], actor())['turns']) == 1
    count = len(model.seen)
    with jobs.database.connect() as db:
        replay = db.execute('SELECT * FROM jobs WHERE id=%s', (queued['id'],)).fetchone()
    assert api.dispatch_job(replay, actor(), lambda p: None) == saved['result']
    assert len(model.seen) == count
    revoked = dict(actor(), roles=[])
    with pytest.raises(ApplicationError):
        api.dispatch_job(replay, revoked, lambda p: None)


def test_warm_cache_refreshes_validity_and_sees_other_process_generation(pilot, isolated_document_archive):
    from agentai.ingestion.extract import chunks
    from agentai.knowledge.store import Store
    from test_required_retrieval import Embeddings
    _, store, _ = pilot
    catalog = isolated_document_archive
    version = catalog.save('a', 'doc', 'a.md', b'old', 'alice')
    rows = [dict(row, version=version, document_id='doc') for row in chunks('old', 'a', 'a.md')]
    store.replace('a', rows, document_id='doc')
    assert store.search('old', ['a'])[0]['validity'] == 'current'
    catalog.metadata(version, 'obsolete', 'changed')
    assert store.search('old', ['a'])[0]['validity'] == 'obsolete'
    other = Store(Embeddings(), store.client)
    new_version = catalog.save('a', 'doc', 'a.md', b'new', 'alice')
    rows = [dict(row, version=new_version, document_id='doc') for row in chunks('new', 'a', 'a.md')]
    other.replace('a', rows, document_id='doc')
    assert store.search('new', ['a'])[0]['text'] == 'new'
    other.remove('a', 'doc', version=new_version)
    assert store.search('new', ['a']) == []
    assert catalog.get(new_version, ['a'])['state'] == 'removed'


def test_removing_superseded_version_cannot_hide_the_current_generation(pilot, isolated_document_archive):
    from agentai.ingestion.extract import chunks
    _, store, _ = pilot
    catalog = isolated_document_archive
    versions = []
    for text in ('old', 'current'):
        version = catalog.save('a', 'doc', 'a.md', text.encode(), 'alice')
        versions.append(version)
        rows = [dict(row, version=version, document_id='doc') for row in chunks(text, 'a', 'a.md')]
        store.replace('a', rows, document_id='doc')
    with pytest.raises(ApplicationError):
        store.remove('a', 'doc', version=versions[0])
    assert store.search('current', ['a'])[0]['text'] == 'current'
    assert catalog.get(versions[1], ['a'])['state'] == 'indexed'


def test_failure_before_history_commit_does_not_complete_the_job(pilot, monkeypatch):
    client, _, _ = pilot
    queued = client.post('/jobs/chat', headers=headers(), json={'question': 'Вопрос'}).json()
    jobs = Jobs(Database())
    def unavailable(*args, **kwargs):
        raise OSError('simulated failure before history transaction')
    monkeypatch.setattr(Conversations, 'append', unavailable)
    assert Worker(jobs, Capacity(), api.dispatch_job).process_one()
    saved = jobs.get(queued['id'], actor())
    assert saved['state'] == 'failed' and saved['result'] is None
    assert Conversations(Database()).get(saved['conversation_id'], actor())['turns'] == []


def test_job_list_uses_one_connection_and_preserves_revoked_access(monkeypatch):
    from contextlib import contextmanager
    from psycopg.types.json import Jsonb
    database = Database()
    jobs = Jobs(database)
    user = actor(sources=['a', 'b'])
    public = jobs.enqueue('index', {'version': 'public'}, user, 'a')
    private = jobs.enqueue('index', {'version': 'private'}, user, 'b')
    chat = jobs.enqueue('chat', {'conversation_id': 'conversation'}, user)
    with database.connect() as db:
        db.execute("UPDATE jobs SET state='succeeded',result=%s WHERE id=%s",
                   (Jsonb({'answer': 'private text', 'access_sources': ['b']}), chat['id']))
    connect = database.connect
    calls = []
    @contextmanager
    def counted():
        calls.append(1)
        with connect() as connection:
            yield connection
    monkeypatch.setattr(database, 'connect', counted)
    found = {row['id']: row for row in jobs.list(actor(sources=['a']))}
    assert len(calls) == 1
    assert public['id'] in found and private['id'] not in found
    assert found[chat['id']]['result'] is None
    assert all('payload' not in row for row in found.values())

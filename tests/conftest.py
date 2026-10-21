import pytest
import os
import uuid
from agentai import api
from agentai.documents import DocumentArchive


@pytest.fixture(autouse=True)
def isolated_document_archive(tmp_path, monkeypatch):
    pg = os.getenv('TEST_DATABASE_URL')
    schema = 'test_'+uuid.uuid4().hex
    if pg:
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        with psycopg.connect(pg,autocommit=True) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        monkeypatch.setenv('DATABASE_URL',make_conninfo(pg,options='-c search_path='+schema))
    else:
        monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ENABLE_JOB_WORKER','false')
    monkeypatch.setenv('QUEUE_WEBUI_REQUESTS','false')
    api.database.cache_clear()
    if pg:
        api.database()
    catalog=DocumentArchive(tmp_path/'documents')
    monkeypatch.setattr(api,'archive',lambda:catalog)
    yield catalog
    api.database.cache_clear()
    if pg:
        with psycopg.connect(pg,autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

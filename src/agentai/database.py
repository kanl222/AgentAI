"""PostgreSQL state for the single-worker pilot; originals stay in the file volume."""
import hashlib
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def principal(user):
    return hashlib.sha256((user.get('name', 'anonymous') + ':' + user.get('sha256', '')).encode()).hexdigest()


class Database:
    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ['DATABASE_URL']
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(72900431)')
            db.execute('''CREATE TABLE IF NOT EXISTS versions (
                version TEXT PRIMARY KEY, document_id TEXT NOT NULL, source TEXT NOT NULL,
                filename TEXT NOT NULL, sha256 TEXT NOT NULL, size BIGINT NOT NULL,
                uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL, state TEXT NOT NULL,
                validity TEXT NOT NULL DEFAULT 'current', note TEXT NOT NULL DEFAULT '')''')
            db.execute('CREATE INDEX IF NOT EXISTS versions_source ON versions(source,document_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            db.execute('''CREATE TABLE IF NOT EXISTS turns (
                id BIGSERIAL PRIMARY KEY, conversation UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                question TEXT NOT NULL, answer TEXT NOT NULL, sources JSONB NOT NULL, context JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id UUID PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, source TEXT,
                state TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
                payload JSONB NOT NULL, result JSONB, error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,created_at)')
            db.execute('''CREATE TABLE IF NOT EXISTS index_heads (
                source TEXT NOT NULL, scope TEXT NOT NULL, generation TEXT,
                PRIMARY KEY(source,scope))''')

    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            db.execute("SET LOCAL statement_timeout = '30s'")
            yield db

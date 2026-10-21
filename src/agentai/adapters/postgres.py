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

    def migrate(self):
        from ..migrations import migrate
        migrate(self)

    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            db.execute("SET LOCAL statement_timeout = '30s'")
            yield db

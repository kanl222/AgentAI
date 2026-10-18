"""Atomic visibility switch after staging all vectors; PostgreSQL is authoritative."""
from .database import Database
import os


class IndexHeads:
    def __init__(self, in_memory=False):
        self.database = Database() if os.getenv('DATABASE_URL') and not in_memory else None
        self.memory = {}

    def snapshot(self):
        if not self.database:
            return dict(self.memory)
        with self.database.connect() as db:
            return {(r['source'],r['scope']): r['generation'] for r in db.execute('SELECT * FROM index_heads').fetchall()}

    def activate(self, source, scope, generation, version=None):
        if not self.database:
            self.memory[source,scope] = generation
            return
        with self.database.connect() as db:
            db.execute('''INSERT INTO index_heads VALUES (%s,%s,%s)
                ON CONFLICT(source,scope) DO UPDATE SET generation=excluded.generation''', (source,scope,generation))
            if version:
                db.execute("UPDATE versions SET state='superseded' WHERE source=%s AND document_id=%s AND state='indexed'", (source,scope))
                db.execute("UPDATE versions SET state='indexed' WHERE version=%s", (version,))

    @staticmethod
    def visible(row, heads):
        scope = row.get('_scope', row.get('document_id', '*'))
        key = row['source'],scope
        if key in heads:
            return row.get('_generation') == heads[key] and heads[key] is not None
        # Failed/uncommitted staging generations must never become searchable.
        return '_generation' not in row

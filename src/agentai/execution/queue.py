"""Durable execution Jobs."""
import os
import uuid
from psycopg.types.json import Jsonb
from ..errors import ApplicationError
from ..adapters.postgres import principal

class Jobs:
    def __init__(self, database): self.database = database

    def enqueue(self, kind, payload, user, source=None):
        identity = str(uuid.uuid4())
        with self.database.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(72900433)')
            size = db.execute("SELECT count(*) AS n FROM jobs WHERE state IN ('queued','running')").fetchone()['n']
            if size >= max(1,min(100,int(os.getenv('MAX_PENDING_JOBS','32')))):
                raise ApplicationError(429,'Job queue is full')
            db.execute('INSERT INTO jobs(id,owner,kind,source,payload) VALUES (%s,%s,%s,%s,%s)',
                       (identity,principal(user),kind,source,Jsonb(payload)))
        return {'id':identity,'state':'queued','progress':0}

    def get(self, identity, user):
        with self.database.connect() as db:
            row = db.execute('SELECT id,kind,source,state,progress,result,error,created_at,updated_at,payload FROM jobs WHERE id=%s AND owner=%s', (identity,principal(user))).fetchone()
        return self._present(row, user)

    @staticmethod
    def _present(row, user):
        if not row or (row['source'] and row['source'] not in user['sources']):
            raise ApplicationError(404,'Job not found')
        if row['result'] and not set(row['result'].get('access_sources',[])) <= set(user['sources']):
            row['result'] = None
        payload = row.pop('payload')
        if row['kind']=='chat':
            row['conversation_id'] = payload.get('conversation_id')
        row['id'] = str(row['id'])
        return row

    def list(self, user):
        with self.database.connect() as db:
            found = db.execute('''SELECT id,kind,source,state,progress,result,error,created_at,updated_at,payload
                FROM jobs WHERE owner=%s ORDER BY created_at DESC LIMIT 100''', (principal(user),)).fetchall()
        rows = []
        for row in found:
            try: rows.append(self._present(row,user))
            except ApplicationError: pass
        return rows

    def cancel(self, identity, user):
        self.get(identity,user)
        with self.database.connect() as db:
            row = db.execute("UPDATE jobs SET state='cancelled',updated_at=now() WHERE id=%s AND state='queued' RETURNING kind,payload", (identity,)).fetchone()
            if row and row['kind']=='index':
                db.execute("UPDATE versions SET state='failed' WHERE version=%s AND state='pending'", (row['payload']['version'],))
        if not row: raise ApplicationError(409,'Only queued jobs can be cancelled')

    def claim(self):
        with self.database.connect() as db:
            return db.execute('''UPDATE jobs SET state='running',progress=1,updated_at=now()
                WHERE id=(SELECT id FROM jobs WHERE state='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING *''').fetchone()

    def progress(self, identity, value):
        with self.database.connect() as db:
            db.execute('UPDATE jobs SET progress=%s,updated_at=now() WHERE id=%s', (value,identity))

    def finish(self, identity, result=None, error=None):
        with self.database.connect() as db:
            db.execute("UPDATE jobs SET state=%s,progress=100,result=%s,error=%s,updated_at=now() WHERE id=%s AND state='running'",
                       ('failed' if error else 'succeeded',Jsonb(result) if result else None,error,identity))

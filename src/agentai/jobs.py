"""Durable bounded PostgreSQL job queue, one GPU consumer, permissions rechecked."""
import json
import os
import threading
import uuid

import psycopg
from psycopg.types.json import Jsonb
from fastapi import HTTPException

from .database import principal


class Jobs:
    def __init__(self, database): self.database = database

    def enqueue(self, kind, payload, user, source=None):
        identity = str(uuid.uuid4())
        with self.database.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(72900433)')
            size = db.execute("SELECT count(*) AS n FROM jobs WHERE state IN ('queued','running')").fetchone()['n']
            if size >= max(1,min(100,int(os.getenv('MAX_PENDING_JOBS','32')))):
                raise HTTPException(429,'Job queue is full')
            db.execute('INSERT INTO jobs(id,owner,kind,source,payload) VALUES (%s,%s,%s,%s,%s)',
                       (identity,principal(user),kind,source,Jsonb(payload)))
        return {'id':identity,'state':'queued','progress':0}

    def get(self, identity, user):
        with self.database.connect() as db:
            row = db.execute('SELECT id,kind,source,state,progress,result,error,created_at,updated_at FROM jobs WHERE id=%s AND owner=%s', (identity,principal(user))).fetchone()
        if not row or (row['source'] and row['source'] not in user['sources']):
            raise HTTPException(404,'Job not found')
        if row['result'] and not set(row['result'].get('access_sources',[])) <= set(user['sources']):
            row['result'] = None
        row['id'] = str(row['id'])
        return row

    def list(self, user):
        with self.database.connect() as db:
            ids = db.execute('SELECT id FROM jobs WHERE owner=%s ORDER BY created_at DESC LIMIT 100', (principal(user),)).fetchall()
        rows = []
        for row in ids:
            try: rows.append(self.get(row['id'],user))
            except HTTPException: pass
        return rows

    def cancel(self, identity, user):
        self.get(identity,user)
        with self.database.connect() as db:
            row = db.execute("UPDATE jobs SET state='cancelled',updated_at=now() WHERE id=%s AND state='queued' RETURNING kind,payload", (identity,)).fetchone()
            if row and row['kind']=='index':
                db.execute("UPDATE versions SET state='failed' WHERE version=%s AND state='pending'", (row['payload']['version'],))
        if not row: raise HTTPException(409,'Only queued jobs can be cancelled')

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
            db.execute('UPDATE jobs SET state=%s,progress=100,result=%s,error=%s,updated_at=now() WHERE id=%s',
                       ('failed' if error else 'succeeded',Jsonb(result) if result else None,error,identity))


class Worker:
    def __init__(self, jobs, capacity, dispatch):
        self.jobs,self.capacity,self.dispatch = jobs,capacity,dispatch
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run,daemon=True,name='agentai-jobs')

    def start(self): self.thread.start()
    def close(self): self.stop.set(); self.thread.join(timeout=5)

    def process_one(self):
        if not self.capacity.acquire(blocking=False): return False
        try:
            job = self.jobs.claim()
            if not job: return False
            users = json.loads(os.getenv('AGENT_USERS','[]'))
            user = next((u for u in users if principal(u)==job['owner']),None)
            try:
                if not user: raise PermissionError('Account removed')
                result = self.dispatch(job,user,lambda p:self.jobs.progress(job['id'],p))
                self.jobs.finish(job['id'],result=result)
            except Exception:
                if job['kind']=='index':
                    with self.jobs.database.connect() as db:
                        db.execute("UPDATE versions SET state='failed' WHERE version=%s AND state='pending'", (job['payload']['version'],))
                self.jobs.finish(job['id'],error='Операция не выполнена. Проверьте права, файл и доступность сервисов; затем повторите задание.')
            return True
        finally:
            self.capacity.release()

    def run(self):
        # Session lock prevents two API processes from recovering/running the same queue.
        while not self.stop.is_set():
            try:
                with psycopg.connect(self.jobs.database.dsn,autocommit=True,connect_timeout=5) as leader:
                    if not leader.execute('SELECT pg_try_advisory_lock(72900432)').fetchone()[0]:
                        self.stop.wait(1)
                        continue
                    with self.jobs.database.connect() as db:
                        db.execute("UPDATE jobs SET state='queued',progress=0 WHERE state='running'")
                    while not self.stop.is_set():
                        leader.execute('SELECT 1')
                        try: worked = self.process_one()
                        except Exception: worked = False
                        if not worked: self.stop.wait(0.5)
            except psycopg.Error:
                self.stop.wait(1)

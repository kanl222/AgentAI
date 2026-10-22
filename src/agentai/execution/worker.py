"""Durable execution Worker."""
import json
import os
import threading
import logging
import psycopg
from ..errors import ApplicationError
from ..adapters.postgres import principal

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
            except Exception as error:
                logging.getLogger(__name__).warning('Job failed: id=%s kind=%s error_type=%s',
                                                    job['id'],job['kind'],type(error).__name__)
                if job['kind']=='index':
                    with self.jobs.database.connect() as db:
                        db.execute("UPDATE versions SET state='failed' WHERE version=%s AND state='pending'", (job['payload']['version'],))
                detail = (str(error.detail) if job['kind'] == 'chat' and job['payload'].get('attachments')
                          and isinstance(error, ApplicationError) and error.status_code in (413, 422)
                          else 'Операция не выполнена. Проверьте права, файл и доступность сервисов; затем повторите задание.')
                self.jobs.finish(job['id'],error=detail)
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
                        pending = leader.execute("SELECT EXISTS(SELECT 1 FROM jobs WHERE state='queued')").fetchone()[0]
                        try: worked = self.process_one() if pending else False
                        except Exception: worked = False
                        if not worked: self.stop.wait(0.5)
            except psycopg.Error:
                self.stop.wait(1)

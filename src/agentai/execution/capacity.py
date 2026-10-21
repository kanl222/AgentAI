"""One GPU operation across local threads and configured API processes."""
import os
import threading

import psycopg

from ..errors import ApplicationError


class Capacity:
    lock_key = 72900434

    def __init__(self):
        self.local = threading.BoundedSemaphore(1)
        self.owner = threading.local()

    def acquire(self, blocking=True):
        if not self.local.acquire(blocking=blocking):
            return False
        connection = None
        try:
            dsn = os.getenv('DATABASE_URL')
            if dsn:
                connection = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
                # Never wait on another process while holding up a request or a worker.
                acquired = connection.execute('SELECT pg_try_advisory_lock(%s)', (self.lock_key,)).fetchone()[0]
                if not acquired:
                    connection.close()
                    self.local.release()
                    return False
            self.owner.connection = connection
            return True
        except Exception:
            if connection is not None:
                connection.close()
            self.local.release()
            raise ApplicationError(503, 'Operation coordination unavailable; check PostgreSQL') from None

    def release(self):
        connection = getattr(self.owner, 'connection', None)
        try:
            if connection is not None:
                # Session locks are released even if the operation or connection failed.
                connection.close()
        finally:
            self.owner.connection = None
            self.local.release()

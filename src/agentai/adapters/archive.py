"""Originals on disk; PostgreSQL catalogue with legacy SQLite support for migration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import uuid
import os
from .postgres import Database


_AUTO = object()


class DocumentArchive:
    def __init__(self, root, database=_AUTO):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = (Database() if os.getenv('DATABASE_URL') else None) if database is _AUTO else database
        if not self.database:
            self.initialize_legacy_catalog()

    def initialize_legacy_catalog(self):
        """SQLite compatibility mode; PostgreSQL schema belongs to migrations."""
        with self.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS versions (
                version TEXT PRIMARY KEY, document_id TEXT NOT NULL, source TEXT NOT NULL,
                filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL, state TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS versions_source ON versions(source, document_id)')

    @contextmanager
    def connection(self):
        if self.database:
            class Adapter:
                def __init__(self, db): self.db = db
                def execute(self, query, params=()):
                    return self.db.execute(query.replace('?', '%s'), params)
            with self.database.connect() as db:
                yield Adapter(db)
            return
        db = sqlite3.connect(self.root/'catalog.sqlite3', timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, source, document_id, filename, data, user):
        version = str(uuid.uuid4())
        path = self.root/(version+'.bin')
        path.write_bytes(data)
        try:
            with self.connection() as db:
                db.execute('INSERT INTO versions (version,document_id,source,filename,sha256,size,uploaded_at,uploaded_by,state) VALUES (?,?,?,?,?,?,?,?,?)', (
                    version, document_id, source, filename, hashlib.sha256(data).hexdigest(),
                    len(data), datetime.now(timezone.utc).isoformat(), user, 'pending'))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return version

    def finish(self, version, success):
        with self.connection() as db:
            if success:
                row = db.execute('SELECT source,document_id FROM versions WHERE version=?', (version,)).fetchone()
                db.execute("UPDATE versions SET state='superseded' WHERE source=? AND document_id=? AND state='indexed'", (row['source'],row['document_id']))
            if success:
                db.execute("UPDATE versions SET state='indexed' WHERE version=?", (version,))
            else:
                db.execute("UPDATE versions SET state='failed' WHERE version=? AND state<>'indexed'", (version,))

    def list(self, sources, limit=50, offset=0):
        if not sources:
            return []
        placeholders = ','.join('?' for _ in sources)
        with self.connection() as db:
            rows = db.execute(f'SELECT * FROM versions WHERE source IN ({placeholders}) ORDER BY uploaded_at DESC,version DESC LIMIT ? OFFSET ?',
                              (*sources,limit,offset)).fetchall()
        return [dict(row) for row in rows]

    def get(self, version, sources):
        if not sources:
            return None
        placeholders = ','.join('?' for _ in sources)
        with self.connection() as db:
            row = db.execute(f'SELECT * FROM versions WHERE version=? AND source IN ({placeholders})', (version,*sources)).fetchone()
        return dict(row) if row else None

    def path(self, version):
        return self.root/(str(uuid.UUID(version))+'.bin')

    def metadata(self, version, validity, note):
        if not self.database:
            raise RuntimeError('Document management requires PostgreSQL')
        with self.database.connect() as db:
            db.execute('UPDATE versions SET validity=%s,note=%s WHERE version=%s', (validity,note,version))

    def remove_from_search(self, version):
        with self.connection() as db:
            db.execute("UPDATE versions SET state='removed' WHERE version=?", (version,))

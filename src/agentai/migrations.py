"""Versioned, additive PostgreSQL schema initialization."""


def migrate(database):
    with database.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(72900431)')
        db.execute('CREATE TABLE IF NOT EXISTS schema_versions (\n            version INTEGER PRIMARY KEY, installed_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        applied = {row['version'] for row in db.execute('SELECT version FROM schema_versions').fetchall()}
        if 1 not in applied:
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
            db.execute('INSERT INTO schema_versions(version) VALUES (1)')

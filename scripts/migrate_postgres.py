"""Copy and verify the legacy SQLite catalogue; keep SQLite and original files intact."""
import hashlib
import os
from pathlib import Path
import sqlite3

from agentai.database import Database


def migrate(root, database):
    path = Path(root)/'catalog.sqlite3'
    if not path.exists(): return 0
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as old:
        old.row_factory = sqlite3.Row
        rows = [dict(row) for row in old.execute('SELECT * FROM versions').fetchall()]
    names = ['version','document_id','source','filename','sha256','size','uploaded_at','uploaded_by','state']
    with database.connect() as db:
        for row in rows:
            original = Path(root)/(row['version']+'.bin')
            if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=row['sha256']:
                raise ValueError('Original missing or checksum mismatch; migration rolled back')
            db.execute('INSERT INTO versions ('+','.join(names)+') VALUES ('+','.join(['%s']*len(names))+') ON CONFLICT(version) DO NOTHING', tuple(row[name] for name in names))
            copied = db.execute('SELECT * FROM versions WHERE version=%s', (row['version'],)).fetchone()
            if any(copied[name]!=row[name] for name in names if name!='state'):
                raise ValueError('Catalogue mismatch; migration rolled back')
        count = db.execute('SELECT count(*) AS n FROM versions WHERE version=ANY(%s)', ([row['version'] for row in rows],)).fetchone()['n']
        if count!=len(rows): raise ValueError('Row count mismatch')
    return count


if __name__=='__main__':
    print('Verified migrated original versions:',migrate(Path(os.getenv('DOCUMENTS_DIR','data/documents')).resolve(),Database()))

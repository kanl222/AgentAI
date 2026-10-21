"""Persistent, principal-scoped conversation memory with source revalidation."""
import os
import uuid

from ..errors import ApplicationError
from psycopg.types.json import Jsonb

from ..adapters.postgres import principal


class Conversations:
    def __init__(self, database):
        self.database = database

    def prune(self):
        days = max(1, min(365, int(os.getenv('CONVERSATION_RETENTION_DAYS', '30'))))
        with self.database.connect() as db:
            db.execute("DELETE FROM jobs WHERE kind='chat' AND payload->>'conversation_id' IN (SELECT id::text FROM conversations WHERE updated_at < now() - (%s * interval '1 day'))", (days,))
            db.execute("DELETE FROM conversations WHERE updated_at < now() - (%s * interval '1 day')", (days,))

    def create(self, user, title='Новый диалог'):
        self.prune()
        identity = str(uuid.uuid4())
        with self.database.connect() as db:
            db.execute('INSERT INTO conversations(id,owner,title) VALUES (%s,%s,%s)', (identity,principal(user),title[:120]))
        return identity

    def get(self, identity, user, display=False):
        self.prune()
        with self.database.connect() as db:
            row = db.execute('SELECT id,title,updated_at FROM conversations WHERE id=%s AND owner=%s', (identity,principal(user))).fetchone()
            if not row:
                raise ApplicationError(404, 'Conversation not found')
            turns = db.execute('SELECT question,answer,sources,context FROM turns WHERE conversation=%s ORDER BY id DESC LIMIT %s', (identity,40 if display else 12)).fetchall()
        # Drop the whole turn when permissions changed, including the user's question.
        safe = [turn for turn in reversed(turns) if set(turn['context'].get('access_sources', [])) <= set(user['sources'])
                and all(item['source'] in user['sources'] for item in turn['sources'])]
        if display:
            row['id'] = str(row['id'])
            row['turns'] = safe
            return row
        # Preserve recent complete turns within the small model's context budget.
        bounded, size = [], 0
        for turn in reversed(safe):
            cost = len(turn['question']) + len(turn['answer'])
            if size + cost > 6000:
                if not bounded:
                    shortened = dict(turn,question=turn['question'][:1500],answer=turn['answer'][:4000]+'\n[Предыдущий ответ сокращён для контекста.]')
                    bounded.insert(0,shortened)
                break
            bounded.insert(0, turn)
            size += cost
        row['id'] = str(row['id'])
        row['turns'] = bounded
        return row

    def append(self, identity, user, question, result, job_id=None):
        self.get(identity, user)
        sources = [{'id': row['id'], 'source': row['source'], 'citation': row['citation']} for row in result.get('sources', [])]
        context = {'access_sources': user['sources'], 'selected': result.get('selected_sources', [])}
        with self.database.connect() as db:
            if job_id:
                job = db.execute('''SELECT state,result FROM jobs
                    WHERE id=%s AND owner=%s AND kind='chat'
                    AND payload->>'conversation_id'=%s FOR UPDATE''',
                    (job_id, principal(user), str(identity))).fetchone()
                if not job:
                    raise ApplicationError(404, 'Job not found')
                if job['state'] == 'succeeded':
                    return job['result']
                if job['state'] != 'running':
                    raise ApplicationError(409, 'Chat job must be running')
            db.execute('INSERT INTO turns(conversation,question,answer,sources,context) VALUES (%s,%s,%s,%s,%s)',
                       (identity,question,result['answer'],Jsonb(sources),Jsonb(context)))
            db.execute("UPDATE conversations SET updated_at=now(), title=CASE WHEN title='Новый диалог' THEN %s ELSE title END WHERE id=%s AND owner=%s", (question[:120],identity,principal(user)))
            db.execute('''DELETE FROM turns WHERE conversation=%s AND id NOT IN
                       (SELECT id FROM turns WHERE conversation=%s ORDER BY id DESC LIMIT 40)''', (identity,identity))

            if job_id:
                db.execute("UPDATE jobs SET state='succeeded',progress=100,result=%s,error=NULL,updated_at=now() WHERE id=%s",
                           (Jsonb(result), job_id))
        return result

    def list(self, user):
        self.prune()
        with self.database.connect() as db:
            rows = db.execute('SELECT id,title,updated_at FROM conversations WHERE owner=%s ORDER BY updated_at DESC LIMIT 100', (principal(user),)).fetchall()
        for row in rows: row['id'] = str(row['id'])
        return rows

    def delete(self, identity, user):
        self.get(identity,user)
        with self.database.connect() as db:
            db.execute("DELETE FROM jobs WHERE owner=%s AND kind='chat' AND payload->>'conversation_id'=%s", (principal(user),identity))
            db.execute('DELETE FROM conversations WHERE id=%s AND owner=%s', (identity,principal(user)))

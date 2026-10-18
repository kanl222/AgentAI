"""Operator smoke test using synthetic data; run inside the configured API container."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time

import httpx


def main(mode):
    path=Path('/app/data/pilot-verification.json')
    reader=os.environ['WEBUI_READER_KEY']
    uploader=os.environ['DOCUMENTS_INDEXER_KEY']
    def request(method,route,body=None,key=None):
        with httpx.Client(timeout=240) as client:
            response=client.request(method,'http://127.0.0.1:8000'+route,json=body,
                                    headers={'Authorization':'Bearer '+(key or reader)})
            response.raise_for_status()
            return response.json()
    def wait(identity,key=None):
        for _ in range(240):
            result=request('GET','/jobs/'+identity,key=key)
            if result['state'] in {'failed','cancelled'}: raise RuntimeError('Smoke job failed')
            if result['state']=='succeeded': return result['result']
            time.sleep(1)
        raise TimeoutError('Smoke job timeout')
    if mode=='prepare':
        text='Учебная проверка пилота. Ответственный за тест — команда Pilot. Все данные искусственные.'
        job=request('POST','/jobs/documents',{'source':'docs-demo','filename':'pilot-verification.txt',
                    'content_base64':base64.b64encode(text.encode()).decode()},uploader)
        upload=wait(job['id'],uploader)
        version=upload['version']
        for route in ['/documents/'+version+'/reindex','/documents/'+version]:
            job=request('POST' if route.endswith('reindex') else 'DELETE',route,{} if route.endswith('reindex') else None,uploader)
            wait(job['id'],uploader)
        original=request('GET','/documents')['versions']
        assert any(row['version']==version and row['state']=='removed' for row in original)
        with httpx.Client() as client:
            restored=client.get('http://127.0.0.1:8000/documents/'+version+'/original',headers={'Authorization':'Bearer '+reader})
            assert restored.status_code==200 and hashlib.sha256(restored.content).digest()==hashlib.sha256(text.encode()).digest()
        identity=request('POST','/conversations',{})['id']
        first=request('POST','/chat',{'question':'Объясни min_total из source-bundle.md. Кратко.','conversation_id':identity})
        assert first['sources'] and all('source-bundle.md' in row['citation'] for row in first['sources'])
        path.write_text(json.dumps({'conversation_id':identity,'version':version}),encoding='utf-8')
        print(json.dumps({'upload_reindex_remove':True,'original_sha256':True,'first_turn_persisted':True}))
    elif mode=='queue-interrupted':
        from agentai.core import authenticate
        from agentai.database import Database
        from agentai.jobs import Jobs
        data=json.loads(path.read_text(encoding='utf-8'))
        user=authenticate('Bearer '+reader)
        jobs=Jobs(Database())
        job=jobs.enqueue('chat',{'question':'А когда применяется этот порог? Кратко.','conversation_id':data['conversation_id']},user)
        with jobs.database.connect() as db:
            db.execute("UPDATE jobs SET state='running',progress=10 WHERE id=%s", (job['id'],))
        data['job_id']=job['id']
        path.write_text(json.dumps(data),encoding='utf-8')
        print('Interrupted job persisted while API stopped')
    elif mode=='verify':
        data=json.loads(path.read_text(encoding='utf-8'))
        result=wait(data['job_id'])
        assert result['conversation_id']==data['conversation_id']
        assert result['sources'] and all('source-bundle.md' in row['citation'] for row in result['sources'])
        assert 'после' in result['answer'].lower() and ('сумм' in result['answer'].lower() or 'агрег' in result['answer'].lower())
        history=request('GET','/conversations/'+data['conversation_id'])
        assert len(history['turns'])==2
        request('DELETE','/conversations/'+data['conversation_id'])
        print(json.dumps({'recovered_job_succeeded':True,'conversation_survived_restart':True,'followup_scope_preserved':True,'history_deleted':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','queue-interrupted','verify'])
    main(parser.parse_args().mode)

"""Repair this installation's reader configuration and LM Studio endpoint safely."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil


def configure(path):
    lines=path.read_text(encoding='utf-8-sig').splitlines()
    values={}
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            name,value=line.split('=',1)
            value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":
                value=value[1:-1]
            values[name.strip()]=value
    key=values.get('WEBUI_READER_KEY','')
    if not key or key.startswith('replace-'):
        raise ValueError('Configure WEBUI_READER_KEY first')
    try:
        users=json.loads(values.get('AGENT_USERS','[]'))
        if not isinstance(users,list):
            raise ValueError('Expected array')
    except ValueError:
        users=[]
    digest=hashlib.sha256(key.encode()).hexdigest()
    if not any(isinstance(u,dict) and u.get('sha256')==digest for u in users):
        users.append({'name':'webui-reader','sha256':digest,'roles':['reader'],'sources':['docs-demo','repo-demo']})
    changes={
        'AGENT_USERS':"'"+json.dumps(users,separators=(',',':'))+"'",
        'LMSTUDIO_BASE_URL':'http://26.151.107.141:1234/v1',
        'CHAT_MODEL':'qwen/qwen3-4b-2507',
        'EMBED_MODEL':'text-embedding-nomic-embed-text-v1.5',
    }
    backup=path.with_name(path.name+'.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    shutil.copy2(path,backup)
    for name,value in changes.items():
        indexes=[i for i,line in enumerate(lines) if line.partition('=')[0].strip()==name]
        if indexes:
            for i in indexes:
                lines[i]=name+'='+value
        else:
            lines.append(name+'='+value)
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Updated reader configuration, LM Studio endpoint and model IDs; keys retained.')
    print('Backup:',backup.name)


if __name__=='__main__':
    configure(Path(__file__).resolve().parents[1]/'.env')

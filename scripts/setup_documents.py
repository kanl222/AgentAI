"""Create a dedicated document uploader key in the administrator's .env."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil


def read_env(path):
    lines=path.read_text(encoding='utf-8-sig').splitlines()
    values={}
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            name,value=line.split('=',1)
            value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":
                value=value[1:-1]
            values[name.strip()]=value
    return lines,values


def setup(path,source):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',source):
        raise ValueError('Invalid source ID')
    lines,values=read_env(path)
    users=json.loads(values.get('AGENT_USERS','[]'))
    reader_digest=hashlib.sha256(values.get('WEBUI_READER_KEY','').encode()).hexdigest()
    if not any(u.get('sha256')==reader_digest and 'reader' in u.get('roles',[]) and source in u.get('sources',[]) for u in users):
        raise ValueError('WebUI reader must already have access to this source')
    key=values.get('DOCUMENTS_INDEXER_KEY') or secrets.token_hex(32)
    if key==values.get('WEBUI_READER_KEY'):
        raise ValueError('Uploader key must be separate from WebUI reader key')
    digest=hashlib.sha256(key.encode()).hexdigest()
    matching=[u for u in users if u.get('sha256')==digest]
    if matching:
        for user in matching:
            user['roles']=sorted(set(user['roles'])|{'indexer'})
            user['sources']=sorted(set(user['sources'])|{source})
    else:
        if any(u.get('name')=='documents-uploader' for u in users):
            raise ValueError('documents-uploader already exists with another key; inspect configuration')
        users.append({'name':'documents-uploader','sha256':digest,'roles':['indexer'],'sources':[source]})
    changes={'AGENT_USERS':"'"+json.dumps(users,separators=(',',':'))+"'",'DOCUMENTS_INDEXER_KEY':key}
    backup=path.with_name(path.name+'.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    shutil.copy2(path,backup)
    for name,value in changes.items():
        indexes=[i for i,line in enumerate(lines) if line.partition('=')[0].strip()==name]
        if indexes:
            for i in indexes:lines[i]=name+'='+value
        else:lines.append(name+'='+value)
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Dedicated uploader configured for source:',source)
    print('Keys retained in .env and not printed. Backup:',backup.name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',type=Path,default=Path('.env'))
    parser.add_argument('--source',default='docs-demo')
    args=parser.parse_args()
    setup(args.env,args.source)

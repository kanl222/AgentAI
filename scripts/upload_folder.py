"""Upload a document folder recursively; preserve other documents and skip unchanged files."""
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

SUPPORTED={'.pdf','.docx','.txt','.md'}
EXCLUDED={'.git','.venv','node_modules','bin','obj','__pycache__'}
MAX_BYTES=20_000_000


def collect(root):
    root=root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Expected a directory')
    visited={root}
    files=[]
    def fail(error):
        raise error
    for current,dirs,names in os.walk(root,topdown=True,followlinks=False,onerror=fail):
        current=Path(current)
        allowed=[]
        for name in sorted(dirs):
            child=current/name
            resolved=child.resolve()
            if (name.startswith('.') or name.lower() in EXCLUDED or child.is_symlink()
                    or not resolved.is_relative_to(root) or resolved in visited):
                continue
            visited.add(resolved)
            allowed.append(name)
        dirs[:]=allowed
        for name in sorted(names):
            path=current/name
            if (not name.startswith('.') and path.suffix.lower() in SUPPORTED
                    and not path.is_symlink() and path.resolve().is_relative_to(root)):
                files.append(path)
    return files


def identity(source,relative,namespace=''):
    logical=(namespace.strip('/')+'/' if namespace else '')+relative.as_posix()
    return uuid.uuid5(uuid.NAMESPACE_URL,source+'/'+logical).hex


def request(url,key,body=None):
    req=Request(url,data=None if body is None else json.dumps(body).encode(),headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urlopen(req,timeout=1800) as response:
        return json.load(response)


def manifest(base,key,source):
    result={}
    offset=0
    while True:
        rows=request(base+'/documents?limit=200&offset='+str(offset),key)['versions']
        for row in rows:
            if row['source']==source and row['state']=='indexed':
                result.setdefault(row['document_id'],row['sha256'])
        if len(rows)<200:
            return result
        offset+=200


def upload_all(root,source,files,base,upload_key,known,namespace=''):
    counts={'uploaded':0,'unchanged':0,'failed':0}
    for path in files:
        relative=path.relative_to(root)
        try:
            if path.stat().st_size>MAX_BYTES:
                raise ValueError('File exceeds 20 MB')
            data=path.read_bytes()
            if len(data)>MAX_BYTES:
                raise ValueError('File exceeds 20 MB')
            docid=identity(source,relative,namespace)
            if known.get(docid)==hashlib.sha256(data).hexdigest():
                counts['unchanged']+=1
                print('Unchanged:',relative,flush=True)
                continue
            result=request(base+'/ingest/document',upload_key,{'source':source,'filename':path.name,
                'document_id':docid,'content_base64':base64.b64encode(data).decode()})
            counts['uploaded']+=1
            print('Uploaded:',relative,'chunks:',result['chunks'],flush=True)
        except HTTPError as error:
            counts['failed']+=1
            print('Failed:',relative,'HTTP',error.code,flush=True)
            # Do not retry a queue when all subsequent requests would fail authorization.
            if error.code in {401,403}:
                raise
        except (OSError,ValueError) as error:
            counts['failed']+=1
            print('Failed:',relative,type(error).__name__,str(error),flush=True)
    return counts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    parser.add_argument('--source',required=True)
    parser.add_argument('--namespace',default='',help='Separate folder identity within a source; keep stable between runs')
    parser.add_argument('--env',type=Path,help='Administrator only: local .env with uploader and reader keys')
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--dry-run',action='store_true',help='List files without reading credentials or making requests')
    args=parser.parse_args()
    try:
        root=args.folder.resolve(strict=True)
        files=collect(root)
    except (OSError,ValueError) as error:
        parser.error(str(error))
    print('Supported documents:',len(files),flush=True)
    if args.dry_run:
        for path in files:
            print(path.relative_to(root),'[over 20 MB]' if path.stat().st_size>MAX_BYTES else '')
        return
    if not files:
        parser.error('No PDF, DOCX, TXT or MD files found')
    if args.env:
        from setup_documents import read_env
        _,values=read_env(args.env)
        upload_key=values.get('DOCUMENTS_INDEXER_KEY','')
        reader_key=values.get('WEBUI_READER_KEY','')
        if not upload_key or not reader_key:
            parser.error('Run scripts/setup_documents.py for this source first')
    else:
        upload_key=reader_key=getpass.getpass('Personal backend key with reader and indexer roles: ')
    base=args.url.rstrip('/')
    try:
        known=manifest(base,reader_key,args.source)
        counts=upload_all(root,args.source,files,base,upload_key,known,args.namespace)
    except (HTTPError,URLError,TimeoutError) as error:
        print('Connection/access error:',getattr(error,'code',type(error).__name__))
        raise SystemExit(1) from None
    print(json.dumps(counts),flush=True)
    if counts['failed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()

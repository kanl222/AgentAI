"""Upload, list and download original document versions using backend permissions."""
import argparse
import base64
import getpass
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--env',type=Path,help='Administrator only: read upload/reader key from this local .env file')
    commands=parser.add_subparsers(dest='command',required=True)
    upload=commands.add_parser('upload')
    upload.add_argument('file',type=Path)
    upload.add_argument('--source',required=True)
    upload.add_argument('--document-id')
    listing=commands.add_parser('list')
    listing.add_argument('--offset',type=int,default=0)
    download=commands.add_parser('download')
    download.add_argument('version',type=uuid.UUID)
    download.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    path='/documents?offset='+str(args.offset) if args.command=='list' else '/documents/'+str(getattr(args,'version',''))+'/original'
    data=None
    if args.command=='upload':
        if args.file.stat().st_size>20_000_000:
            parser.error('Maximum file size: 20 MB')
        body={'source':args.source,'filename':args.file.name,'content_base64':base64.b64encode(args.file.read_bytes()).decode()}
        if args.document_id:body['document_id']=args.document_id
        data=json.dumps(body).encode()
        path='/ingest/document'
    if args.command=='download' and args.output.exists():
        parser.error('Output exists; choose a new filename')
    if args.env:
        from setup_documents import read_env
        _,values=read_env(args.env)
        field='DOCUMENTS_INDEXER_KEY' if args.command=='upload' else 'WEBUI_READER_KEY'
        key=values.get(field,'')
        if not key:parser.error(field+' is missing; run scripts/setup_documents.py first')
    else:
        key=getpass.getpass('Backend API key (indexer for upload, reader for list/download): ')
    request=Request(args.url.rstrip('/')+path,data=data,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=1800) as response:
            result=response.read()
    except HTTPError as error:
        print('HTTP',error.code,error.read().decode(errors='replace'))
        raise SystemExit(1) from None
    if args.command=='download':
        with args.output.open('xb') as target:target.write(result)
        print('Saved:',args.output)
    else:
        print(json.dumps(json.loads(result),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()

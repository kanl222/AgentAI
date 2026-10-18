import hashlib
import hmac
import json
import os
import re
import uuid
from io import BytesIO
from pathlib import Path
from threading import RLock
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi
from .retrieval import scope_records
from .indexing import IndexHeads


def authenticate(header):
    if not header or not header.startswith('Bearer '):
        raise HTTPException(401, 'Bearer key required')
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    try:
        users = json.loads(os.getenv('AGENT_USERS', '[]'))
        if not isinstance(users, list):
            raise ValueError('Expected users array')
        for entry in users:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get('name'), str)
                    or not isinstance(entry.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                    or not all(isinstance(entry.get(field), list)
                               and all(isinstance(value, str) for value in entry[field])
                               for field in ('roles', 'sources'))):
                raise ValueError('Invalid user record')
    except (ValueError, TypeError):
        raise HTTPException(503, 'Invalid AGENT_USERS configuration; contact the administrator') from None
    for user in users:
        if hmac.compare_digest(digest, user['sha256']):
            return user
    raise HTTPException(401, 'Invalid key')


def require(user, role, source=None):
    if role not in user['roles'] or (source is not None and source not in user['sources']):
        raise HTTPException(403, 'Access denied')


def chunks(text, source, location):
    lines = text.splitlines()
    pending, start, end, count = [], 1, 1, 0
    def row(body,first,last):
        citation = f'{location}#L{first}-L{last}'
        return {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,source+citation+body)),
                'source':source,'text':body,'citation':citation}
    for number,line in enumerate(lines,1):
        for part in [line[i:i+3000] for i in range(0,len(line),3000)] or ['']:
            if pending and (count+len(part)>3500 or number-start>=50):
                body='\n'.join(pending)
                if body.strip():
                    yield row(body,start,end)
                pending,count=[],0
            if not pending:
                start=number
            pending.append(part)
            count+=len(part)+1
            end=number
    body='\n'.join(pending)
    if body.strip():
        yield row(body,start,end)


def document(data, filename, source):
    suffix = Path(filename).suffix.lower()
    location = f'document:{source}/{Path(filename).name}'
    if suffix == '.pdf':
        from pypdf import PdfReader
        for page, item in enumerate(PdfReader(BytesIO(data)).pages, 1):
            yield from chunks(item.extract_text() or '', source, f'{location}?page={page}')
    elif suffix == '.docx':
        from docx import Document
        from docx.table import Table
        doc = Document(BytesIO(data))
        paragraph_index, table_index = 0,0
        for item in doc.iter_inner_content():
            if isinstance(item,Table):
                table_index += 1
                header = [cell.text for cell in item.rows[0].cells] if item.rows else []
                for ri,row in enumerate(item.rows,1):
                    cells = [cell.text for cell in row.cells]
                    text = ' | '.join(cells) if ri==1 else ' | '.join(f'{h}: {value}' for h,value in zip(header,cells))
                    yield from chunks(text,source,f'{location}?table={table_index}&row={ri}')
            else:
                paragraph_index += 1
                yield from chunks(item.text,source,f'{location}?paragraph={paragraph_index}')
    elif suffix in {'.txt', '.md'}:
        yield from chunks(data.decode('utf-8-sig'), source, location)
    else:
        raise ValueError('Supported: PDF/DOCX/TXT/MD')


def gitlab(source):
    config = json.loads(os.getenv('GITLAB_SOURCES', '{}'))[source]
    project = int(config['project'])
    with httpx.Client(base_url=os.environ['GITLAB_URL'].rstrip('/')+'/api/v4/',
                      headers={'PRIVATE-TOKEN':os.environ['GITLAB_TOKEN']}, timeout=30,
                      follow_redirects=False) as client:
        def get(path, **params):
            response = client.get(f'projects/{project}/repository/'+path, params=params)
            response.raise_for_status()
            return response
        sha = get('commits/'+quote(config.get('ref','main'),safe='')).json()['id']
        if not re.fullmatch('[a-f0-9]{40,64}', sha):
            raise ValueError('Invalid commit')
        extensions = {'.py','.cs','.java','.js','.ts','.tsx','.jsx','.go','.rs','.cpp','.c','.h','.hpp','.sql','.sh','.yaml','.yml','.json','.xml','.md','.vue','.kt','.rb','.php','.fs','.vb','.toml'}
        page = 1
        while True:
            response = get('tree', ref=sha, recursive='true', per_page=100, page=page)
            for item in response.json():
                path = Path(item['path'])
                if item['type'] != 'blob' or path.suffix.lower() not in extensions:
                    continue
                if any(p in {'.git','vendor','node_modules','bin','obj'} for p in path.parts):
                    continue
                blob = get('files/'+quote(item['path'],safe='')+'/raw',ref=sha).content
                if len(blob) <= 512000 and b'\0' not in blob:
                    yield from chunks(blob.decode('utf-8',errors='replace'),source,
                                      f"gitlab:{project}@{sha}/{item['path']}")
            if not response.headers.get('x-next-page'):
                break
            page = int(response.headers['x-next-page'])


def tokenize(text):
    return re.findall(r'\w+',text.lower()) or ['_']


def lexical_tokens(text):
    """Retain exact identifiers and add Russian prefix features for inflected words."""
    words = tokenize(text)
    return words + ['ru:'+word[:6] for word in words if len(word)>=7 and re.fullmatch('[а-яё]+',word)]


class Store:
    def __init__(self, embeddings, client=None, index_heads=None):
        self.embeddings = embeddings
        self.client = client or QdrantClient(url=os.getenv('QDRANT_URL','http://qdrant:6333'))
        self.lock = RLock()
        self.collection = 'agentai'
        self.heads = index_heads or IndexHeads()

    @staticmethod
    def acl(sources):
        return models.Filter(must=[models.FieldCondition(key='source',match=models.MatchAny(any=list(sources)))])

    def replace(self, source, rows, document_id=None, progress=None):
        rows = list(rows)
        if not rows:
            raise ValueError('No text; scanned PDFs require OCR')
        generation = str(uuid.uuid4())
        scope = document_id or '*'
        with self.lock:
            for start in range(0,len(rows),16):
                batch = [dict(row) for row in rows[start:start+16]]
                vectors = self.embeddings.embed_documents([r['text'] for r in batch])
                if len(vectors)!=len(batch): raise ValueError('Incomplete embedding batch')
                if not self.client.collection_exists(self.collection):
                    self.client.create_collection(self.collection,vectors_config=models.VectorParams(size=len(vectors[0]),distance=models.Distance.COSINE))
                for row in batch:
                    row.update(_scope=scope,_generation=generation)
                    row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL,generation+row['id']))
                self.client.upsert(self.collection,points=[models.PointStruct(id=r['id'],vector=v,payload=r) for r,v in zip(batch,vectors)],wait=True)
                if progress: progress(10+int(80*min(start+16,len(rows))/len(rows)))
            self.heads.activate(source,scope,generation,rows[0].get('version'))
        return len(rows)

    def remove(self, source, document_id):
        with self.lock: self.heads.activate(source,document_id,None)

    def corpus(self, sources):
        if not sources or not self.client.collection_exists(self.collection): return []
        points, offset = [],None
        while True:
            batch,offset = self.client.scroll(self.collection,scroll_filter=self.acl(sources),limit=256,offset=offset,with_payload=True,with_vectors=False)
            points.extend(batch)
            if offset is None: break
        heads = self.heads.snapshot()
        points = [p for p in points if self.heads.visible(p.payload,heads)]
        if self.heads.database:
            with self.heads.database.connect() as db:
                info = {r['version']:r for r in db.execute('SELECT version,validity,note FROM versions WHERE source=ANY(%s)', (list(sources),)).fetchall()}
            for point in points:
                item = info.get(point.payload.get('version'))
                if item: point.payload.update(validity=item['validity'],note=item['note'])
        return points

    def read_source(self, row_id, sources, offset=0, limit=3):
        from .retrieval import document_key
        points = self.corpus(sources)
        target = next((p.payload for p in points if p.payload['id']==row_id),None)
        if not target: return []
        same = [p.payload for p in points if document_key(p.payload)==document_key(target)]
        same.sort(key=lambda row:[int(n) if n.isdigit() else n for n in re.split(r'(\d+)',row['citation'])])
        index = next(i for i,row in enumerate(same) if row['id']==row_id)
        start = max(0,index+max(-2,min(100,offset)))
        return same[start:start+max(1,min(6,limit))]

    def search(self, query, sources):
        original_query = query
        aliases = json.loads(os.getenv('SEARCH_ALIASES','{}'))
        additions = [term for key,values in aliases.items() if key.lower() in query.lower() for term in values if isinstance(term,str)]
        query = (query+' '+' '.join(additions))[:2000]
        if not sources or not self.client.collection_exists(self.collection):
            return []
        with self.lock:
            corpus = self.corpus(sources)
            if not corpus:
                return []
            scoped, targeted = scope_records([p.payload for p in corpus], original_query)
            allowed_ids = {row['id'] for row in scoped}
            corpus = [p for p in corpus if p.payload['id'] in allowed_ids]
            if not corpus:
                return []
            active_filter = self.acl(sources)
            active_filter.must.append(models.HasIdCondition(has_id=[p.id for p in corpus]))
            dense = self.client.query_points(self.collection,query=self.embeddings.embed_query(query),query_filter=active_filter,limit=20).points
            dense = [p for p in dense if p.payload['id'] in allowed_ids]
            scores = BM25Okapi([lexical_tokens(p.payload['text']) for p in corpus]).get_scores(lexical_tokens(query))
            lexical = [corpus[i] for i in sorted(range(len(corpus)),key=lambda i:scores[i],reverse=True)[:20] if scores[i]>0]
            fused, records = {}, {}
            canonical = {p.id:p.payload for p in corpus}
            if targeted:
                # Include all chunks of the explicit target even outside dense top 20.
                for point in corpus:
                    fused[point.id] = 1
                    records[point.id] = point.payload
            for ranking in (dense,lexical):
                for rank,p in enumerate(ranking,1):
                    fused[p.id] = fused.get(p.id,0)+1/(60+rank)
                    records[p.id] = canonical[p.id]
            # Short exact phrases such as a document title must survive dense noise.
            query_terms = tokenize(query)
            if 3 <= len(query_terms) <= 16:
                phrase = ' '.join(query_terms)
                for point in corpus:
                    if phrase in ' '.join(tokenize(point.payload['text'])):
                        fused[point.id] = fused.get(point.id,0)+1
                        records[point.id] = point.payload
            return [records[k] for k in sorted(fused,key=fused.get,reverse=True)[:6]]


def sql_metadata():
    if not os.getenv('SQL_CONNECTION_STRING'):
        return 'SQL disabled'
    import pyodbc
    with pyodbc.connect(os.environ['SQL_CONNECTION_STRING'],timeout=5,autocommit=True) as connection:
        cursor = connection.cursor()
        cursor.timeout = 10
        cursor.execute('SELECT TOP (500) TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION')
        return [tuple(row) for row in cursor.fetchall()]

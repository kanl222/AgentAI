"""Read a GitLab source pinned to one commit."""
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
import httpx
from ..ingestion.extract import chunks

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

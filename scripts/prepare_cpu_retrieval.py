"""Download pinned public Q8 models and the CPU-only runtime for retrieval comparison."""
import json
import os
from pathlib import Path
import zipfile
import httpx
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '30'
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]/'work/cpu-retrieval-comparison'
api = HfApi(token=False)
repos = ['nomic-ai/nomic-embed-text-v1.5-GGUF', 'Qwen/Qwen3-Embedding-0.6B-GGUF',
         'ggml-org/Qwen3-reranker-0.6B-Q8_0-GGUF']
manifest = {}
for repo in repos:
    revisions = {
        'nomic-ai/nomic-embed-text-v1.5-GGUF': '0188c9bf409793f810680a5a431e7b899c46104c',
        'Qwen/Qwen3-Embedding-0.6B-GGUF': '370f27d7550e0def9b39c1f16d3fbaa13aa67728',
        'ggml-org/Qwen3-reranker-0.6B-Q8_0-GGUF': 'a02f48bb4f057028298c21fa033da2b30d7742d5',
    }
    info = api.model_info(repo, revision=revisions[repo])
    files = [file.rfilename for file in info.siblings if file.rfilename.lower().endswith('.gguf') and 'q8_0' in file.rfilename.lower()]
    print(json.dumps({'repo': repo, 'revision': info.sha, 'q8_files': files}), flush=True)
    assert len(files) == 1, files
    filename = files[0]
    path = ROOT/'models'/repo.split('/')[-1]/filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix('.partial')
        with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
            url = f'https://huggingface.co/{repo}/resolve/{info.sha}/{filename}'
            head = client.head(url)
            head.raise_for_status()
            size = int(head.headers['content-length'])
            offset = temporary.stat().st_size if temporary.exists() else 0
            with temporary.open('ab') as output:
                while offset < size:
                    end = min(offset + 8*1024*1024 - 1, size-1)
                    download = client.get(url, headers={'Range': f'bytes={offset}-{end}'})
                    download.raise_for_status()
                    assert download.status_code == 206
                    assert download.headers['content-range'].startswith(f'bytes {offset}-{end}/')
                    assert len(download.content) == end-offset+1
                    output.write(download.content)
                    output.flush()
                    offset = end+1
                    if offset % (64*1024*1024) == 0 or offset == size:
                        print(json.dumps({'repo': repo, 'downloaded_mib': round(offset/2**20), 'total_mib': round(size/2**20)}), flush=True)
        temporary.replace(path)
    manifest[repo] = {'revision': info.sha, 'filename': filename, 'path': str(Path(path).resolve()), 'bytes': Path(path).stat().st_size}
    (ROOT/'models.json').write_text(json.dumps(manifest, indent=2))
with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
    response = client.get('https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11669')
    response.raise_for_status()
    release = response.json()
    if not any(asset['name'].endswith('.zip') for asset in release['assets']):
        tag_asset = next(asset for asset in release['assets'] if asset['name'] == 'nightly-tag.txt')
        tag = client.get(tag_asset['browser_download_url']).text.strip()
        response = client.get('https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/'+tag)
        response.raise_for_status()
        release = response.json()
    matches = [asset for asset in release['assets'] if asset['name'].endswith('bin-win-cpu-x64.zip')]
    print(json.dumps({'llama_cpp_release': release['tag_name'], 'assets': [asset['name'] for asset in matches]}), flush=True)
    assert len(matches) == 1
    asset = matches[0]
    path = ROOT/asset['name']
    with client.stream('GET', asset['browser_download_url']) as download:
        download.raise_for_status()
        with path.open('wb') as output:
            for chunk in download.iter_bytes():
                output.write(chunk)
    target = ROOT/'runtime'
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            resolved = (target/member.filename).resolve()
            assert resolved.is_relative_to(target.resolve())
        archive.extractall(target)
    manifest['runtime'] = {'release': release['tag_name'], 'asset': asset['name'],
                           'url': asset['browser_download_url'], 'commit': release['target_commitish']}
    (ROOT/'models.json').write_text(json.dumps(manifest, indent=2))
print('Downloads complete', flush=True)

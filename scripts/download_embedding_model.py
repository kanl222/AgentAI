"""Install the pinned Qwen embedding GGUF without changing application settings."""
import argparse
import hashlib
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REVISION = '370f27d7550e0def9b39c1f16d3fbaa13aa67728'
SHA256 = '06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439'
SIZE = 639150592
URL = f'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/{REVISION}/Qwen3-Embedding-0.6B-Q8_0.gguf'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def install(target, source=None):
    if target.exists():
        if digest(target) != SHA256:
            raise ValueError('Existing model has another checksum; move it aside before installing')
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.partial')
    if source:
        if digest(source) != SHA256:
            raise ValueError('Source model checksum mismatch')
        shutil.copyfile(source, temporary)
    else:
        with temporary.open('wb') as output:
            for offset in range(0, SIZE, 8*1024*1024):
                end = min(offset+8*1024*1024-1, SIZE-1)
                request = Request(URL, headers={'Range': f'bytes={offset}-{end}'})
                with urlopen(request, timeout=60) as response:
                    data = response.read()
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {offset}-{end}/{SIZE}' or len(data) != end-offset+1:
                        raise ValueError('Unexpected download range')
                    output.write(data)
    if temporary.stat().st_size != SIZE or digest(temporary) != SHA256:
        raise ValueError('Downloaded model checksum mismatch')
    temporary.replace(target)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Reuse an already downloaded file with the pinned checksum')
    parser.add_argument('--target', type=Path, default=ROOT/'data/models/qwen3-embedding-0.6b-q8_0.gguf')
    args = parser.parse_args()
    print({'installed': install(args.target, args.source), 'path': str(args.target), 'sha256': SHA256})

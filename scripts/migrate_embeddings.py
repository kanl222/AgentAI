"""Re-embed active payloads into a new collection; pause API writes until cutover."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from qdrant_client import QdrantClient
from agentai.adapters.lmstudio import lmstudio_clients
from agentai.execution.capacity import Capacity
from agentai.knowledge.heads import IndexHeads
from agentai.knowledge.migration import reembed_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    capacity = Capacity()
    if not capacity.acquire(blocking=False):
        raise SystemExit('Another operation is running; stop writes before migration')
    try:
        embeddings, _ = lmstudio_clients()
        client = QdrantClient(url=os.getenv('QDRANT_URL', 'http://qdrant:6333'))
        report = reembed_collection(client, embeddings, IndexHeads(), args.source, args.target,
                                   lambda current, total: print(json.dumps({'completed': current, 'total': total}), flush=True))
        print(json.dumps(report), flush=True)
    finally:
        capacity.release()


if __name__ == '__main__':
    main()

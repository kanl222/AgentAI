"""Evaluate the configured embedding model on a separate in-memory corpus."""
import argparse
import json
import os
from pathlib import Path

from qdrant_client import QdrantClient
from agentai.api import lmstudio_clients
from agentai.core import Store, document
from agentai.indexing import IndexHeads


def evaluate(dataset, embeddings):
    store = Store(embeddings,QdrantClient(':memory:'),index_heads=IndexHeads(in_memory=True))
    for item in dataset['documents']:
        store.replace(item['id'],document(item['text'].encode(),item['filename'],item['id']))
    sources = [item['id'] for item in dataset['documents']]
    results = []
    for case in dataset['cases']:
        rows = store.search(case['query'],sources)
        rank = next((i for i,row in enumerate(rows,1) if row['source']==case['expected']),None)
        results.append({'query':case['query'],'expected':case['expected'],'rank':rank,'top_sources':[row['source'] for row in rows]})
    return {'model':os.getenv('EMBED_MODEL'),'cases':len(results),
            'recall_at_3':sum(bool(r['rank'] and r['rank']<=3) for r in results)/len(results),
            'mrr_at_6':sum(1/r['rank'] if r['rank'] else 0 for r in results)/len(results),'results':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=Path('examples/rag-evaluation.json'))
    parser.add_argument('--output',type=Path,default=Path('work/rag-evaluation.json'))
    args=parser.parse_args()
    result=evaluate(json.loads(args.dataset.read_text(encoding='utf-8')),lmstudio_clients()[0])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='results'}))

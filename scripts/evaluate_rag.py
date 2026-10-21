"""Evaluate the configured embedding model on a separate in-memory corpus."""
import argparse
import json
import os
from pathlib import Path

from qdrant_client import QdrantClient
from agentai.api import lmstudio_clients
from agentai.core import Store, document
from agentai.indexing import IndexHeads
from agentai.execution.capacity import Capacity


def evaluate(dataset, embeddings):
    store = Store(embeddings,QdrantClient(':memory:'),index_heads=IndexHeads(in_memory=True))
    for item in dataset['documents']:
        store.replace(item['id'],document(item['text'].encode(),item['filename'],item['id']))
    sources = [item['id'] for item in dataset['documents']]
    results = []
    if not dataset['cases']:
        raise ValueError('Evaluation requires at least one case')
    for case in dataset['cases']:
        rows = store.search(case['query'],case.get('sources', sources))
        def relevant(row):
            return (row['source']==case['expected'] and
                    all(term in row['text'] for term in case.get('expected_text', [])))
        rank = next((i for i,row in enumerate(rows,1) if relevant(row)),None)
        results.append({'query':case['query'],'expected':case['expected'],'rank':rank,
                        'passed': not rows if case['expected'] is None else rank is not None,
                        'top_sources':[row['source'] for row in rows]})
    positive = [r for r in results if r['expected'] is not None]
    return {'model':os.getenv('EMBED_MODEL'),'cases':len(results),
            'pass_rate':sum(r['passed'] for r in results)/len(results),
            'recall_at_3':sum(bool(r['rank'] and r['rank']<=3) for r in positive)/len(positive) if positive else None,
            'mrr_at_6':sum(1/r['rank'] if r['rank'] and r['rank']<=6 else 0 for r in positive)/len(positive) if positive else None,
            'results':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=Path('examples/rag-evaluation.json'))
    parser.add_argument('--output',type=Path,default=Path('work/rag-evaluation.json'))
    args=parser.parse_args()
    capacity = Capacity()
    if not capacity.acquire(blocking=False):
        raise SystemExit('GPU busy; retry evaluation after the current operation')
    try:
        result=evaluate(json.loads(args.dataset.read_text(encoding='utf-8')),lmstudio_clients()[0])
    finally:
        capacity.release()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='results'}))

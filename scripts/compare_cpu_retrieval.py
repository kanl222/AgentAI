"""Isolated CPU-only embedding/reranking comparison on the frozen AgentAI corpus.

Run with work/cpu-retrieval-comparison/.venv/Scripts/python.exe.
Does not connect to the application, PostgreSQL, Qdrant or LM Studio.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import threading
import time

import httpx
import numpy as np
import psutil
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'work/cpu-retrieval-comparison'
REPOS = {'nomic': 'nomic-ai/nomic-embed-text-v1.5-GGUF',
         'qwen': 'Qwen/Qwen3-Embedding-0.6B-GGUF',
         'rerank': 'ggml-org/Qwen3-reranker-0.6B-Q8_0-GGUF'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def inputs():
    spec = importlib.util.spec_from_file_location('pilot', ROOT/'scripts/autorag_pilot.py')
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    corpus, questions, _, source = pilot.load_inputs()
    lex_spec = importlib.util.spec_from_file_location('lexical', ROOT/'src/agentai/knowledge/text.py')
    lexical = importlib.util.module_from_spec(lex_spec)
    lex_spec.loader.exec_module(lexical)
    manifest = {'corpus_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'questions_sha256': hashlib.sha256(json.dumps(questions, sort_keys=True).encode()).hexdigest(),
                'lexical_sha256': hashlib.sha256((ROOT/'src/agentai/knowledge/text.py').read_bytes()).hexdigest(),
                'chunks': len(corpus), 'documents': len({r['document_id'] for r in corpus}),
                'questions': len(questions), 'threads': 6, 'device': 'CPU', 'quantization': 'Q8_0',
                'candidate_count': 20, 'final_count': 6, 'generation_evaluated': False,
                'started_at': datetime.now(timezone.utc).isoformat()}
    if (OUTPUT/'manifest.json').exists():
        previous = read(OUTPUT/'manifest.json')
        for key in ('corpus_sha256', 'questions_sha256', 'lexical_sha256'):
            assert previous[key] == manifest[key], 'Inputs changed; use a new output directory'
    else:
        save(OUTPUT/'manifest.json', manifest)
        save(OUTPUT/'questions.json', questions)
    return corpus, questions, lexical


@contextmanager
def server(kind, port):
    manifest = read(OUTPUT/'models.json')
    executable, = (OUTPUT/'runtime').rglob('llama-server.exe')
    model = manifest[REPOS[kind]]['path']
    args = [str(executable), '-m', model, '--host', '127.0.0.1', '--port', str(port),
            '-ngl', '0', '--device', 'none', '-t', '6', '-tb', '6', '-c', '4096',
            '-b', '4096', '-ub', '4096' if kind == 'nomic' else '256', '-np', '1',
            '--embedding', '--pooling', {'nomic': 'mean', 'qwen': 'last', 'rerank': 'rank'}[kind],
            '--no-cache-prompt']
    if kind == 'rerank':
        args.append('--reranking')
    if kind == 'nomic':
        # Official Nomic GGUF guidance uses YaRN .75 beyond the 2048 metadata.
        # Current server also caps slots to metadata; update only that advertised
        # cap, keeping the original context explicit for YaRN's calculation.
        args.extend(['--override-kv', 'nomic-bert.context_length=int:8192',
                     '--rope-scaling', 'yarn', '--rope-freq-scale', '.75', '--yarn-orig-ctx', '2048'])
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('LLAMA_ARG_', 'HF_TOKEN', 'LLAMA_API_KEY'))}
    environment['CUDA_VISIBLE_DEVICES'] = '-1'
    environment['GGML_VK_VISIBLE_DEVICES'] = '-1'
    stats = {'command': args, 'peak_rss_bytes': 0, 'min_available_bytes': psutil.virtual_memory().available}
    stop = threading.Event()
    with (OUTPUT/f'{kind}-server.log').open('wb') as log:
        process = subprocess.Popen(args, stdout=log, stderr=log, env=environment,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        owned = psutil.Process(process.pid)
        stats['pid'] = process.pid
        def sample():
            low = 0
            while not stop.wait(.25):
                try:
                    info = owned.memory_info()
                    stats['peak_rss_bytes'] = max(stats['peak_rss_bytes'], info.rss, getattr(info, 'peak_wset', 0))
                    free = psutil.virtual_memory().available
                    stats['min_available_bytes'] = min(stats['min_available_bytes'], free)
                    low = low+1 if free < 256*2**20 else 0
                    if low >= 12:
                        stats['stopped_low_memory'] = True
                        process.terminate()
                        return
                except psutil.Error:
                    return
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            start = time.perf_counter()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=300, trust_env=False) as client:
                while time.perf_counter()-start < 90:
                    if process.poll() is not None:
                        raise RuntimeError(f'{kind} server exited; see local server log')
                    try:
                        if client.get('/health', timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.3)
                else:
                    raise TimeoutError('Server start timeout')
                stats['load_seconds'] = time.perf_counter()-start
                yield client, stats
                cpu = owned.cpu_times()
                stats['cpu_seconds'] = cpu.user+cpu.system
        finally:
            stop.set()
            if process.poll() is None:
                stats['shutdown'] = 'terminated_by_harness'
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            thread.join(timeout=2)
            stats['exit_code'] = process.returncode
            save(OUTPUT/f'{kind}-resources.json', stats)


def request(client, path, body):
    start = time.perf_counter()
    response = client.post(path, json=body)
    response.raise_for_status()
    return response.json(), time.perf_counter()-start


def prefix(kind, text, query=False):
    if kind == 'nomic':
        return ('search_query: ' if query else 'search_document: ')+text
    return ('Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:'+text) if query else text


def vector(client, text):
    result, seconds = request(client, '/v1/embeddings', {'input': text, 'encoding_format': 'float'})
    array = np.asarray(result['data'][0]['embedding'], dtype=np.float32)
    assert np.isfinite(array).all() and np.linalg.norm(array) > 0
    return array/np.linalg.norm(array), seconds, result.get('usage', {})


def embed(kind, corpus, questions, lexical, port):
    ranker = BM25Okapi([lexical.lexical_tokens(row['text']) for row in corpus])
    document_vectors = []
    rows = []
    with server(kind, port) as (client, resources):
        # Warmup is excluded from per-query timings.
        vector(client, prefix(kind, 'Проверка поиска', True))
        indexing_start = time.perf_counter()
        for i, row in enumerate(corpus):
            text = prefix(kind, row['text'])
            tokenized, _ = request(client, '/tokenize', {'content': text, 'add_special': True})
            if len(tokenized['tokens']) > 4096:
                raise ValueError('Corpus input exceeds fixed context; do not silently truncate')
            embedding, _, _ = vector(client, text)
            document_vectors.append(embedding)
            if (i+1) % 8 == 0:
                print(json.dumps({'phase': kind, 'indexed': i+1, 'of': len(corpus)}), flush=True)
        matrix = np.stack(document_vectors)
        resources['index_seconds'] = time.perf_counter()-indexing_start
        np.save(OUTPUT/f'{kind}-document-vectors.npy', matrix)
        for q in questions:
            embedding, embedding_seconds, usage = vector(client, prefix(kind, q['query'], True))
            start = time.perf_counter()
            dense_scores = matrix @ embedding
            dense = sorted(range(len(corpus)), key=lambda i: (-float(dense_scores[i]), corpus[i]['id']))[:20]
            scores = ranker.get_scores(lexical.lexical_tokens(q['query']))
            lex = [i for i in sorted(range(len(corpus)), key=lambda i: (-float(scores[i]), corpus[i]['id']))[:20] if scores[i] > 0]
            fused = {}
            for ranking in (dense, lex):
                for position, i in enumerate(ranking, 1):
                    fused[i] = fused.get(i, 0)+1/(60+position)
            terms = lexical.tokenize(q['query'])
            if 3 <= len(terms) <= 16:
                phrase = ' '.join(terms)
                for i, row in enumerate(corpus):
                    if phrase in ' '.join(lexical.tokenize(row['text'])) or lexical.heading_in_question(row['text'], q['query']):
                        fused[i] = fused.get(i, 0)+1
            ranked = sorted(fused, key=lambda i: (-fused[i], corpus[i]['id']))[:20]
            row = {'qid': q['qid'], 'query': q['query'], 'split': q['split'],
                   'dense_ids': [corpus[i]['id'] for i in dense],
                   'candidate_ids': [corpus[i]['id'] for i in ranked],
                   'embedding_seconds': embedding_seconds,
                   'query_seconds': embedding_seconds+time.perf_counter()-start,
                   'usage': usage, 'dimensions': len(embedding)}
            rows.append(row)
            save(OUTPUT/f'{kind}-retrieval.json', rows)
            print(json.dumps({'phase': kind, 'question': q['qid'], 'seconds': round(row['query_seconds'], 3)}), flush=True)


def rerank(corpus, questions, port):
    by_id = {row['id']: row for row in corpus}
    saved = read(OUTPUT/'reranked.json') if (OUTPUT/'reranked.json').exists() else {}
    with server('rerank', port) as (client, resources):
        sanity = []
        for query, documents in [('What is the capital of China?', ['Beijing is the capital of China.', 'Gravity is a physical force.']),
                                 ('Какой город является столицей России?', ['Москва — столица России.', 'В океане живут киты.'])]:
            result, seconds = request(client, '/v1/rerank', {'query': query, 'documents': documents, 'top_n': 2})
            scores = {item['index']: item['relevance_score'] for item in result['results']}
            assert scores[0] > scores[1] and all(math.isfinite(s) for s in scores.values()), scores
            sanity.append({'query': query, 'scores': scores, 'seconds': seconds})
        save(OUTPUT/'reranker-sanity.json', sanity)
        for kind in ('nomic', 'qwen'):
            source = read(OUTPUT/f'{kind}-retrieval.json')
            rows = saved.setdefault(kind, [])
            source_by_id = {item['qid']: item for item in source}
            for row in rows:
                assert row['candidate_ids'] == source_by_id[row['qid']]['candidate_ids'], 'Candidates changed; use a fresh output'
            done = {row['qid'] for row in rows}
            for item in source:
                if item['qid'] in done:
                    continue
                candidates = item['candidate_ids']
                result, seconds = request(client, '/v1/rerank', {
                    'query': item['query'], 'documents': [by_id[i]['text'] for i in candidates], 'top_n': len(candidates)})
                ranked = sorted(result['results'], key=lambda row: (-row['relevance_score'], row['index']))
                assert len(ranked) == len(candidates)
                assert {r['index'] for r in ranked} == set(range(len(candidates)))
                assert all(math.isfinite(r['relevance_score']) for r in ranked)
                row = {'qid': item['qid'], 'candidate_ids': candidates, 'ranked_ids': [candidates[r['index']] for r in ranked],
                       'scores': [r['relevance_score'] for r in ranked], 'rerank_seconds': seconds,
                       'total_query_seconds': item['query_seconds']+seconds}
                rows.append(row)
                save(OUTPUT/'reranked.json', saved)
                print(json.dumps({'phase': kind+'+rerank', 'question': item['qid'], 'seconds': round(seconds, 2)}), flush=True)


def metrics(questions, rankings, k=6):
    records = []
    for q in questions:
        found = rankings[q['qid']][:k]
        assert len(found) == len(set(found)), 'Duplicate retrieval results'
        groups = q['retrieval_gt']
        positions = [next((i for i, identity in enumerate(found, 1) if identity in group), None) for group in groups]
        relevant = {identity for group in groups for identity in group}
        dcg = sum(1/math.log2(position+1) for position in positions if position is not None)
        ideal = sum(1/math.log2(i+2) for i in range(min(k, len(groups))))
        records.append({'qid': q['qid'], 'query': q['query'], 'positions': positions,
                        'complete': all(p is not None for p in positions),
                        'recall': sum(p is not None for p in positions)/len(groups),
                        'mrr': next((1/i for i, identity in enumerate(found, 1) if identity in relevant), 0),
                        'ndcg': dcg/ideal, 'retrieved_ids': found})
    return {'questions': len(records), 'complete': sum(r['complete'] for r in records),
            'recall': statistics.mean(r['recall'] for r in records),
            'mrr': statistics.mean(r['mrr'] for r in records), 'ndcg': statistics.mean(r['ndcg'] for r in records),
            'cases': records}


def summarize(questions):
    results = {}
    reranked = read(OUTPUT/'reranked.json')
    for kind in ('nomic', 'qwen'):
        source = read(OUTPUT/f'{kind}-retrieval.json')
        for reranking in (False, True):
            name = kind+('+rerank' if reranking else '')
            rows = reranked[kind] if reranking else source
            assert len(rows) == len(questions)
            rankings = {r['qid']: r['ranked_ids' if reranking else 'candidate_ids'] for r in rows}
            seconds = [r['total_query_seconds' if reranking else 'query_seconds'] for r in rows]
            results[name] = {'all': metrics(questions, rankings),
                             **{split: metrics([q for q in questions if q['split'] == split], rankings)
                                for split in ('development', 'holdout')},
                             'median_query_seconds': statistics.median(seconds),
                             'p95_query_seconds': float(np.percentile(seconds, 95)), 'max_query_seconds': max(seconds)}
        results[kind]['candidates_at_20'] = metrics(questions, {r['qid']: r['candidate_ids'] for r in source}, 20)
        results[kind]['dense_at_6'] = metrics(questions, {r['qid']: r['dense_ids'] for r in source}, 6)
        results[kind]['resources'] = read(OUTPUT/f'{kind}-resources.json')
    result = {'manifest': read(OUTPUT/'manifest.json'), 'models': read(OUTPUT/'models.json'),
              'results': results, 'reranker_resources': read(OUTPUT/'rerank-resources.json')}
    save(OUTPUT/'results.json', result)
    print(json.dumps({name: {'complete_at_6': value['all']['complete'], 'recall_at_6': value['all']['recall'],
                             'median_seconds': value['median_query_seconds']} for name, value in results.items()}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['nomic', 'qwen', 'rerank', 'summarize'])
    parser.add_argument('--port', type=int, default=18971)
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    corpus, questions, lexical = inputs()
    if args.phase in ('nomic', 'qwen'):
        embed(args.phase, corpus, questions, lexical, args.port)
    elif args.phase == 'rerank':
        rerank(corpus, questions, args.port)
    else:
        summarize(questions)

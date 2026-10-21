"""Isolated AutoRAG Legacy retrieval experiment on a saved, local corpus.

No application database, production index, model, or external LLM is used.
Run with work/autorag-pilot/.venv/Scripts/python.exe. Data stays under work/.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'work' / 'autorag-pilot'

# Reviewed passages from docs/answer-evaluation-20261022.md. Each inner list
# contains alternatives; all outer groups are required for complete evidence.
GOLD = {
    'overview': [['152598af']],
    'account': [['46708280']],
    'wards': [['9319b1ce']],
    'benefits': [['c57da08b'], ['76f84219']],
    'documents': [['a2d92d92']],
    'document-tables': [['a2d92d92']],
    'all-stores': [['77d0394d']],
    'request-modes': [['77d0394d']],
    'report-access': [['05190ae3']],
    'report-pipelines': [['e37aef65']],
    'new-report': [['4e7225cc']],
    'audit': [['81077bd6']],
    'recipe': [['c07e31b6']],
    'support-tools': [['8f3b2772']],
    'support-access': [['8f3b2772']],
    'benefit-removal': [['0931b4fa'], ['c03e5531']],
    'customer-totals': [['c369b24a']],
    'free-numbers': [['f8c03bb4']],
}

# Frozen before any experiment: related questions remain in the same split.
HOLDOUT = {'account', 'wards', 'all-stores', 'request-modes',
           'report-pipelines', 'recipe', 'support-tools', 'support-access'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def load_inputs():
    snapshot = ROOT / 'work' / 'evaluation-20261022-corpus.json'
    corpus = read_json(snapshot)
    historical = []
    for suffix in ('natural', 'extra'):
        historical.extend(read_json(ROOT / 'work' / f'evaluation-20261022-{suffix}.json')['retrieval'])
    ids = {row['id'] for row in corpus}
    assert len(ids) == len(corpus)
    questions = []
    for row in historical:
        if row['id'] not in GOLD:
            continue
        groups = []
        for alternatives in GOLD[row['id']]:
            resolved = []
            for prefix in alternatives:
                matches = [identity for identity in ids if identity.startswith(prefix)]
                if len(matches) != 1:
                    raise ValueError(f'Ambiguous/missing passage: {prefix}')
                resolved.extend(matches)
            groups.append(resolved)
        questions.append({'qid': row['id'], 'query': row['question'],
                          'retrieval_gt': groups, 'generation_gt': [],
                          'split': 'holdout' if row['id'] in HOLDOUT else 'development'})
    assert len(questions) == len(GOLD)
    by_id = {row['id']: row for row in corpus}
    split_docs = {}
    for split in ('development', 'holdout'):
        split_docs[split] = {by_id[i]['document_id'] for q in questions if q['split'] == split
                             for group in q['retrieval_gt'] for i in group}
    assert not split_docs['development'] & split_docs['holdout'], 'Document leakage'
    return corpus, questions, historical, snapshot


def prepare(output):
    import pandas as pd
    corpus, questions, historical, snapshot = load_inputs()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    lexical_hash = hashlib.sha256((ROOT / 'src/agentai/knowledge/text.py').read_bytes()).hexdigest()
    if (output / 'manifest.json').exists():
        previous = read_json(output / 'manifest.json')
        if (previous['snapshot_sha256'], previous['lexical_source_sha256']) != (snapshot_hash, lexical_hash):
            raise ValueError('Corpus/tokenizer changed: use --output with a new trial directory')
    for split in ('development', 'holdout'):
        rows = [{k: v for k, v in q.items() if k != 'split'} for q in questions if q['split'] == split]
        pd.DataFrame(rows).to_parquet(output / f'qa-{split}.parquet', index=False)
    pd.DataFrame([{'doc_id': r['id'], 'contents': r['text'],
                   'metadata': {'citation': r['citation']}} for r in corpus]).to_parquet(
                       output / 'corpus.parquet', index=False)
    write_json(output / 'questions.json', questions)
    write_json(output / 'manifest.json', {
        'snapshot_sha256': snapshot_hash,
        'lexical_source_sha256': lexical_hash,
        'passages': len(corpus), 'documents': len({r['document_id'] for r in corpus}),
        'development': sum(q['split'] == 'development' for q in questions),
        'holdout': sum(q['split'] == 'holdout' for q in questions),
        'generation_evaluated': False,
        'ground_truth': 'Reviewed passage IDs, not filename-only labels; generation_gt empty (unused).',
        'historical_baseline': 'Saved initial search from 2026-10-22, not a fresh run or full answer pipeline.',
        'excluded_absence_queries': [r['id'] for r in historical if not r['expected']],
    })
    print(json.dumps(read_json(output / 'manifest.json')), flush=True)


def metrics(questions, ranked):
    records = []
    for q in questions:
        found = ranked[q['qid']][:6]
        positions = [next((i for i, identity in enumerate(found, 1) if identity in group), None)
                     for group in q['retrieval_gt']]
        records.append({'qid': q['qid'], 'query': q['query'], 'positions': positions,
                        'complete_at_6': all(p is not None for p in positions),
                        'evidence_recall_at_6': sum(p is not None for p in positions) / len(positions),
                        'reciprocal_rank': sum(1 / p if p else 0 for p in positions) / len(positions),
                        'retrieved_ids': found})
    return {'cases': len(records),
            'complete_at_6': sum(r['complete_at_6'] for r in records),
            'mean_evidence_recall_at_6': sum(r['evidence_recall_at_6'] for r in records) / len(records),
            'mrr_at_6': sum(r['reciprocal_rank'] for r in records) / len(records),
            'results': records}


def register_tokenizers():
    from autorag.nodes.lexicalretrieval.bm25 import BM25_TOKENIZER
    from nltk.stem.snowball import SnowballStemmer
    spec = importlib.util.spec_from_file_location('agentai_pilot_text', ROOT / 'src/agentai/knowledge/text.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stemmer = SnowballStemmer('russian')
    BM25_TOKENIZER['agentai_lexical'] = lambda texts: [module.lexical_tokens(t) for t in texts]
    BM25_TOKENIZER['russian_snowball'] = lambda texts: [
        [stemmer.stem(w) if re.fullmatch('[а-я]+', w) else w
         for w in re.findall(r'\w+', t.lower().replace('ё', 'е'))] or ['_'] for t in texts]


def trial(output, split, tokenizers):
    import pandas as pd
    import yaml
    from autorag.evaluator import Evaluator
    project = output / 'trials' / split
    project.mkdir(parents=True, exist_ok=True)
    config = {'node_lines': [{'node_line_name': 'retrieval', 'nodes': [{
        'node_type': 'lexical_retrieval',
        'strategy': {'metrics': ['retrieval_mrr', 'retrieval_recall']},
        'top_k': 6,
        'modules': [{'module_type': 'bm25', 'bm25_tokenizer': tokenizers}],
    }]}]}
    config_path = project / 'pilot.yaml'
    config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
    evaluator = Evaluator(qa_data_path=str(output / f'qa-{split}.parquet'),
                          corpus_data_path=str(output / 'corpus.parquet'), project_dir=str(project))
    evaluator.start_trial(str(config_path), skip_validation=True)
    trial_id = read_json(project / 'trial.json')[-1]['trial_name']
    directory = project / trial_id / 'retrieval' / 'lexical_retrieval'
    summary = pd.read_csv(directory / 'summary.csv')
    print('Trial summary:', summary.to_json(orient='records'), flush=True)
    return directory


def collect(directory, questions):
    import pandas as pd
    results = {}
    summary = pd.read_csv(directory / 'summary.csv').set_index('filename')
    for path in sorted(directory.glob('*.parquet')):
        if path.name.startswith('best_'):
            continue
        frame = pd.read_parquet(path)
        id_column = next((c for c in ('retrieved_ids', 'retrieved_ids_lexical') if c in frame), None)
        if id_column is None:
            continue
        # Native per-module results preserve QA order but omit qid; the winning
        # result has qid, but using only that file would hide other candidates.
        assert len(frame) == len(questions)
        config = ast.literal_eval(summary.loc[path.name, 'module_params'])
        ranked = {q['qid']: list(ids) for q, ids in zip(questions, frame[id_column])}
        scored = metrics(questions, ranked)
        assert abs(scored['mrr_at_6'] - frame['retrieval_mrr'].mean()) < 1e-9
        assert abs(scored['mean_evidence_recall_at_6'] - frame['retrieval_recall'].mean()) < 1e-9
        score_column = next(c for c in ('retrieve_scores', 'retrieve_scores_lexical') if c in frame)
        zero_score_hits = sum(
            score <= 0 and identity in {i for group in q['retrieval_gt'] for i in group}
            for q, ids, scores in zip(questions, frame[id_column], frame[score_column])
            for identity, score in zip(ids, scores))
        results[path.stem] = {'config': config, 'zero_score_gold_hits': int(zero_score_hits), **scored}
    if not results:
        raise RuntimeError(f'No native retrieval outputs found in {directory}')
    return results


def run(output):
    import importlib.metadata
    # Keep caches local; do not load provider credentials from .env.
    os.environ['HF_HOME'] = str(output / 'cache' / 'huggingface')
    os.environ['NLTK_DATA'] = str(output / 'cache' / 'nltk')
    os.environ['LLAMA_INDEX_CACHE_DIR'] = str(output / 'cache' / 'llama-index')
    os.environ['MPLCONFIGDIR'] = str(output / 'cache' / 'matplotlib')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['ANONYMIZED_TELEMETRY'] = 'False'
    register_tokenizers()
    corpus, questions, historical, _ = load_inputs()
    dev = [q for q in questions if q['split'] == 'development']
    held = [q for q in questions if q['split'] == 'holdout']
    started = time.monotonic()
    directory = trial(output, 'development', ['space', 'agentai_lexical', 'russian_snowball'])
    development = collect(directory, dev)
    # Selection uses development only. MRR has fixed k=6; complete evidence breaks ties.
    winner = max(sorted(development), key=lambda name: (
        development[name]['mrr_at_6'], development[name]['complete_at_6']))
    config = development[winner]['config']
    tokenizer = config.get('bm25_tokenizer')
    if tokenizer is None:
        raise RuntimeError(f'Cannot identify winning tokenizer from {config}')
    write_json(output / 'selected.json', {'module': winner, 'tokenizer': tokenizer,
                                         'selection': 'development MRR@6 then complete evidence@6'})
    held_directory = trial(output, 'holdout', [tokenizer])
    holdout = collect(held_directory, held)
    by_citation = {r['citation']: r['id'] for r in corpus}
    historical_rankings = {r['id']: [by_citation.get(x['citation'], x.get('id', 'unknown'))
                                   for x in r['rows']] for r in historical}
    result = {'autorag_version': importlib.metadata.version('AutoRAG'),
              'seconds': round(time.monotonic() - started, 2),
              'manifest': read_json(output / 'manifest.json'),
              'development': development, 'selected': tokenizer, 'holdout': holdout,
              'historical_baseline': {'development': metrics(dev, historical_rankings),
                                      'holdout': metrics(held, historical_rankings)}}
    write_json(output / 'results.json', result)
    write_json(output / 'historical-baseline.json', result['historical_baseline'])
    print(json.dumps({k: result[k] for k in ('autorag_version', 'seconds', 'selected')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--run', action='store_true', help='Run native AutoRAG after preparing local data')
    args = parser.parse_args()
    prepare(args.output)
    if args.run:
        run(args.output)

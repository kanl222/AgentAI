"""Compare deployed AgentAI with a read-only reproduction of Big RAG.

Run inside the API container with a portable Vectra snapshot. Never changes
either index, starts indexing, or saves conversations. This does not invoke the
LM Studio plugin: native plugin access can be denied by the server.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import httpx
from langchain_core.messages import ToolMessage

from agentai.access import authenticate
from agentai.adapters.lmstudio import lmstudio_clients
from agentai.answering.engine import answer
from agentai.execution.capacity import Capacity
from agentai.knowledge.scoping import explicit_targets
from agentai.knowledge.store import Store
from agentai.settings import ModelSettings
from evaluate_unhinted_search import CASES


def search_big(snapshot, vector):
    """Vectra cosine, top-k per shard, threshold, global top-k."""
    norm = math.sqrt(sum(value*value for value in vector))
    merged = []
    for shard in snapshot['shards']:
        hits = []
        for item in shard['items']:
            if len(item['vector']) != len(vector):
                raise ValueError('Embedding dimension mismatch')
            score = sum(a*b for a, b in zip(vector, item['vector'], strict=True))/(norm*item['norm'])
            hits.append({**item['metadata'], 'id': item['id'], 'score': score, 'shard': shard['name']})
        merged.extend(sorted(hits, key=lambda row: row['score'], reverse=True)[:snapshot['limit']])
    return sorted((row for row in merged if row['score'] >= snapshot['threshold']),
                  key=lambda row: row['score'], reverse=True)[:snapshot['limit']]


def big_prompt(rows, question):
    if not rows:
        return ('Important: No relevant content was found in the indexed documents for the user query. '
                'In less than one sentence, inform the user of this. '
                'Then respond to the query to the best of your ability.\n\nUser Query:\n\n'+question)
    context = 'The following passages were found in your indexed documents:\n\n'
    for number, row in enumerate(rows, 1):
        context += f'\nCitation {number} (from {row["fileName"]}, score: {row["score"]:.3f}): "{row["text"]}"\n\n'
    return (context.rstrip()+'\n\nUse the citations above to respond to the user query, only if they are relevant. '
            'Otherwise, respond to the best of your ability without them.\n\nUser Query:\n\n'+question)


class TracedModel:
    def __init__(self, model, trace, kind='selection'):
        self.model, self.trace, self.kind = model, trace, kind

    def bind_tools(self, tools):
        return TracedModel(self.model.bind_tools(tools), self.trace, 'answer')

    def invoke(self, messages):
        visible = []
        for message in messages:
            if isinstance(message, ToolMessage):
                try:
                    visible = json.loads(message.content)
                except (ValueError, TypeError):
                    pass
        result = self.model.invoke(messages)
        self.trace.append({'kind': self.kind, 'evidence': visible,
                           'input_chars': sum(len(str(m.content)) for m in messages),
                           'tools': result.tool_calls, 'output': str(result.content)})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append', dest='case_ids')
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    cases = [case for case in CASES if not args.case_ids or case[0] in args.case_ids]
    assert cases and all(not any(explicit_targets(case[1])) for case in cases)
    settings = ModelSettings.from_env()
    embeddings, model = lmstudio_clients()
    user = authenticate('Bearer '+os.environ['WEBUI_READER_KEY'])
    store, capacity = Store(embeddings), Capacity()
    corpus = [point.payload for point in store.corpus(user['sources'])]
    agent_files = sorted({row['citation'].split('@')[0].rsplit('/', 1)[-1] for row in corpus})
    big_files = sorted({item['metadata']['fileName'] for shard in snapshot['shards'] for item in shard['items']})
    report = {'method': 'deployed_agentai_vs_big_rag_read_only_reproduction',
              'native_plugin_execution': False,
              'sampling': {'model': settings.chat_model, 'temperature': settings.temperature,
                           'top_p': settings.top_p, 'top_k': settings.top_k, 'min_p': settings.min_p,
                           'presence_penalty': settings.presence_penalty, 'max_tokens': settings.max_tokens,
                           'configured_context': settings.context_length, 'seed': None},
              'big_index': {'sha256': hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
                            'chunks': sum(len(s['items']) for s in snapshot['shards']),
                            'files': big_files, 'limit': snapshot['limit'], 'threshold': snapshot['threshold'],
                            'model': snapshot['model'], 'source_manifest': snapshot.get('source_manifest')},
              'agentai_index': {'chunks': len(corpus), 'files': agent_files, 'model': settings.embedding_model},
              'common_files': sorted(set(big_files)&set(agent_files)), 'cases': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    root = settings.base_url.removesuffix('/v1').rstrip('/')
    with httpx.Client(base_url=root, headers={'Authorization': 'Bearer '+settings.api_key}, timeout=240) as client:
        for index, (identity, question, expected, terms) in enumerate(cases):
            record = {'id': identity, 'question': question, 'expected_documents': expected, 'expected_terms': terms}
            report['cases'].append(record)
            # Alternate ordering to reduce systematic warm-up effects; GPU work is sequential.
            for system in (('agentai', 'big_rag') if index % 2 == 0 else ('big_rag', 'agentai')):
                if not capacity.acquire(blocking=False):
                    raise RuntimeError('GPU busy; results saved, retry when available')
                started = time.monotonic()
                result = {}
                try:
                    if system == 'agentai':
                        initial = store.search(question, user['sources'])
                        trace = []
                        response = answer(question, user, store, TracedModel(model, trace))
                        result = {**response, 'initial_hits': initial, 'trace': trace}
                        hits = response['sources']
                        rank = next((i for i, row in enumerate(hits, 1)
                                     if any('/'+prefix in row['citation'] for prefix in expected)), None)
                    else:
                        response = client.post('/v1/embeddings', json={'model': snapshot['model'], 'input': question,
                                                                      'encoding_format': 'float'})
                        response.raise_for_status()
                        vector = response.json()['data'][0]['embedding']
                        rows = search_big(snapshot, vector)
                        prompt = big_prompt(rows, question)
                        response = client.post('/v1/chat/completions', json={
                            'model': settings.chat_model, 'messages': [{'role': 'user', 'content': prompt}],
                            'temperature': settings.temperature, 'top_p': settings.top_p, 'top_k': settings.top_k,
                            'min_p': settings.min_p, 'presence_penalty': settings.presence_penalty,
                            'max_tokens': settings.max_tokens, 'stream': False})
                        response.raise_for_status()
                        payload = response.json()
                        result = {'answer': payload['choices'][0]['message']['content'], 'hits': rows, 'prompt': prompt,
                                  'finish_reason': payload['choices'][0]['finish_reason'], 'usage': payload.get('usage')}
                        if identity == 'report-access':
                            result['query_vector_for_vectra_check'] = vector
                        rank = next((i for i, row in enumerate(rows, 1)
                                     if any(row['fileName'].startswith(prefix) for prefix in expected)), None)
                    body = result['answer'].casefold().replace('ё', 'е')
                    result['automatic_checks'] = {'expected_document_rank': rank,
                        'terms_present': {term: term.casefold().replace('ё', 'е') in body for term in terms},
                        'not_truncated': result.get('finish_reason') != 'length'}
                except Exception as error:
                    result = {'error': type(error).__name__, 'status': getattr(getattr(error, 'response', None), 'status_code', None)}
                finally:
                    capacity.release()
                result['seconds'] = round(time.monotonic()-started, 2)
                record[system] = result
                save()
                print(json.dumps({'case': identity, 'system': system, 'seconds': result['seconds'],
                                  'error': result.get('error'), 'checks': result.get('automatic_checks')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

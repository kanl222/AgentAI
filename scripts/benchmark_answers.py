"""Compare already loaded local models through the same AgentAI answer pipeline.

Read-only against the index; no uploads, conversation writes, or model loading.
Run models sequentially with identical load settings, then restore prior models.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentai.agent import answer
from agentai.api import lmstudio_clients
from agentai.core import Store, authenticate
from audit_documents import CASE_DEFINITIONS, case_checks
from langchain_core.messages import HumanMessage


class Meter:
    def __init__(self, model, calls):
        self.model, self.calls = model, calls

    def bind_tools(self, tools):
        return Meter(self.model.bind_tools(tools), self.calls)

    def invoke(self, messages):
        start = time.perf_counter()
        response = self.model.invoke(messages)
        usage = response.usage_metadata or {}
        self.calls.append({'seconds': time.perf_counter()-start,
                           'input_tokens': usage.get('input_tokens'),
                           'output_tokens': usage.get('output_tokens'),
                           'reasoning_tokens': usage.get('output_token_details', {}).get('reasoning'),
                           'finish_reason': response.response_metadata.get('finish_reason')})
        return response


class RetrievalMeter:
    def __init__(self, store):
        self.store, self.initial_hash = store, None

    def search(self, query, sources):
        rows = self.store.search(query, sources)
        if self.initial_hash is None:
            canonical = [(r['id'], r['citation'], r['text']) for r in rows]
            self.initial_hash = hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode()).hexdigest()
        return rows

    def read_source(self, *args):
        return self.store.read_source(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reasoning-off', action='store_true')
    args = parser.parse_args()
    os.environ['CHAT_MODEL'] = args.model
    embeddings, model = lmstudio_clients()
    if args.reasoning_off:
        model.extra_body = {'reasoning': 'off'}
    store = Store(embeddings)
    user = authenticate('Bearer ' + os.environ['WEBUI_READER_KEY'])
    result = {'model': args.model, 'temperature': model.temperature, 'max_tokens': model.max_tokens,
              'reasoning_off_requested': args.reasoning_off, 'cases': []}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    # Warm-up is excluded from the question latency measurements and retained.
    warm = []
    Meter(model, warm).invoke([HumanMessage(content='Ответь только ОК.')])
    result['warmup'] = warm
    save()
    for index, (prefix, question, terms) in enumerate(CASE_DEFINITIONS):
        filename = next(p.name for p in args.folder.glob('*.pdf') if p.name.startswith(prefix))
        query = question if index == 0 else question + '\nИспользуй документ ' + filename
        calls, retrieval = [], RetrievalMeter(store)
        case = {'index': index, 'question': query, 'expected_filename': filename}
        case['started_at_unix'] = time.time()
        start = time.perf_counter()
        try:
            response = answer(query, user, retrieval, Meter(model, calls),
                              max_steps=max(1, min(8, int(os.getenv('MAX_STEPS', '4')))))
            case.update(response)
            case['checks'] = case_checks(prefix, filename, response, terms)
        except Exception as error:
            case['error'] = type(error).__name__
        case.update(seconds=time.perf_counter()-start, model_calls=calls,
                    initial_evidence_sha256=retrieval.initial_hash, finished_at_unix=time.time())
        result['cases'].append(case)
        save()
        print('Case', index, 'PASS' if case.get('checks') and all(case['checks'].values()) else 'FAIL',
              'seconds', round(case['seconds'], 2), flush=True)
    # The title lookup at index 0 is backend-only. Include failed generation
    # attempts in latency, rather than reporting only fast successful answers.
    generated = result['cases'][1:]
    result['summary'] = {
        'passed': sum(bool(c.get('checks')) and all(c['checks'].values()) for c in result['cases']),
        'total': len(result['cases']), 'generated_cases': len(generated),
        'median_generated_seconds': statistics.median(c['seconds'] for c in generated) if generated else None,
        'total_seconds': sum(c['seconds'] for c in result['cases']),
        'corrections': sum(c.get('validation', {}).get('correction_attempted', False) for c in result['cases']),
        'errors': sum('error' in c for c in result['cases']),
        'reasoning_tokens': sum(call.get('reasoning_tokens') or 0 for c in generated for call in c['model_calls']),
    }
    known_reasoning = [call['reasoning_tokens'] for call in warm +
                       [call for c in generated for call in c['model_calls']] if call['reasoning_tokens'] is not None]
    result['reasoning_off_observed'] = not any(known_reasoning) if known_reasoning else None
    save()
    print(json.dumps(result['summary']), flush=True)


if __name__ == '__main__':
    main()

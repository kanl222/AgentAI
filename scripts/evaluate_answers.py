"""Probe real answer generation against the existing index, without saving chats.

Automatic checks are regression signals. Inspect answers and source excerpts,
and execute proposed code separately before treating a proposal as verified.
"""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import time

from agentai.access import authenticate
from agentai.adapters.lmstudio import lmstudio_clients
from agentai.answering.engine import answer
from agentai.execution.capacity import Capacity
from agentai.knowledge.store import Store
from audit_documents import CASE_DEFINITIONS, case_checks


class Trace:
    def __init__(self, model, calls):
        self.model, self.calls = model, calls

    def bind_tools(self, tools):
        return Trace(self.model.bind_tools(tools), self.calls)

    def invoke(self, messages):
        started = time.perf_counter()
        try:
            response = self.model.invoke(messages)
        except Exception as error:
            self.calls.append({'seconds':round(time.perf_counter()-started,3),
                               'tools':[], 'usage':None, 'error':type(error).__name__,
                               'status':getattr(error,'status_code',None)})
            raise
        self.calls.append({'seconds': round(time.perf_counter()-started, 3),
                           'tools': [call['name'] for call in response.tool_calls],
                           'usage': response.usage_metadata,
                           'finish_reason': response.response_metadata.get('finish_reason')})
        return response


def cases(filenames):
    result = []
    for index, (prefix, question, terms) in enumerate(CASE_DEFINITIONS):
        filename = next((name for name in filenames if name.startswith(prefix)), None)
        result.append({'id': f'doc-{index+1}', 'kind': 'documents', 'prefix': prefix,
                       'filename': filename, 'terms': terms,
                       'question': question if index == 0 else question+'\nИспользуй документ '+str(filename)})
    result.extend([
        {'id': 'missing-file', 'kind': 'absence',
         'question': 'Что написано в deliberately-missing-evaluation-file.pdf? Укажи источник.'},
        {'id': 'missing-fact', 'kind': 'uncertainty', 'filename': 'source-bundle.md',
         'question': 'Какой production SLA и сколько серверов выделено для OPTIMIZATION_DEMO_ORDERS? '
                     'Используй только source-bundle.md. Если этих сведений нет, прямо скажи об этом.'},
        {'id': 'code-analysis', 'kind': 'code', 'filename': 'source-bundle.md',
         'question': 'Проверь summarize_orders в OPTIMIZATION_DEMO_ORDERS из source-bundle.md. '
                     'Каковы сложность, поведение min_total, дубликатов ID клиентов, возвратов и равных сумм? '
                     'Не переписывай код. Укажи исходные строки.'},
        {'id': 'code-proposal', 'kind': 'code', 'filename': 'source-bundle.md',
         'question': 'Предложи небольшое улучшение summarize_orders в OPTIMIZATION_DEMO_ORDERS из source-bundle.md. '
                     'Сохрани Decimal, порог после накопления включительно, отрицательные возвраты, '
                     'первого клиента при дубликате ID и стабильный порядок равных сумм. '
                     'Покажи полный метод с импортами и два законченных теста. Не заявляй, что код исполнен.'},
        {'id': 'code-refunds', 'kind': 'code', 'filename': 'source-bundle.md',
         'question': 'В OPTIMIZATION_DEMO_ORDERS из source-bundle.md предложи отдельную функцию '
                     'summarize_orders_positive(orders, customers), которая возвращает только клиентов '
                     'с итогом строго больше нуля. Поведение исходного summarize_orders не меняй. '
                     'Покажи импорты, полный код функции и два теста: нулевой итог и отрицательный возврат.'},
    ])
    return result


def checks(case, response):
    if case['kind'] == 'documents':
        return case_checks(case['prefix'], case['filename'], response, case['terms'])
    body = response['answer'].split('\n\nSources:')[0]
    common = {'not_truncated': response['finish_reason'] != 'length',
              'backend_validation': response['validation']['passed']}
    if case['kind'] == 'absence':
        return dict(common, no_sources=not response['sources'],
                    admits_absence=bool(re.search(r'не (?:найден|могу|предоставлен)|нет (?:данных|доступ)|отсутств', body, re.I)))
    common['expected_source_used'] = any(case['filename'] in row['citation'] for row in response['used_sources'])
    if case['kind'] == 'code' and case['id'] != 'code-analysis':
        common['python_block'] = bool(re.search(r'```python\s*\n', body))
    return common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case',action='append',dest='selected_cases',help='Run only the named case; repeatable')
    args = parser.parse_args()
    embeddings, model = lmstudio_clients()
    store, capacity = Store(embeddings), Capacity()
    user = authenticate('Bearer '+os.environ['WEBUI_READER_KEY'])
    filenames = sorted({point.payload['citation'].split('/', 1)[-1].split('@')[0]
                        for point in store.corpus(user['sources'])
                        if point.payload['citation'].startswith('document:')})
    report = {'model': os.getenv('CHAT_MODEL'), 'embedding_model': os.getenv('EMBED_MODEL'),
              'max_steps': int(os.getenv('MAX_STEPS', '4')), 'cases': [],
              'mode': 'live_models_existing_index_no_chat_writes'}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for case in cases(filenames):
        if args.selected_cases and case['id'] not in args.selected_cases:
            continue
        record, calls = dict(case), []
        started = time.perf_counter()
        if case.get('filename') is None and case['kind'] == 'documents':
            record['error'] = 'Required source is absent'
        elif not capacity.acquire(blocking=False):
            record['error'] = 'GPU busy; case not run'
        else:
            try:
                response = answer(case['question'], user, store, Trace(model, calls),
                                  max_steps=max(1, min(8, report['max_steps'])))
                record.update(response)
                record['checks'] = checks(case, response)
            except Exception as error:
                record['error'] = type(error).__name__
                message = str(error)[:1200]
                for name in ('LMSTUDIO_API_KEY','WEBUI_READER_KEY'):
                    secret = os.getenv(name)
                    if secret: message = message.replace(secret,'<REDACTED>')
                record['error_message'] = message
            finally:
                capacity.release()
        record.update(seconds=round(time.perf_counter()-started, 3), model_calls=calls)
        report['cases'].append(record)
        save()
        print(json.dumps({'id': case['id'], 'seconds': record['seconds'],
                          'checks': record.get('checks'), 'error': record.get('error')}, ensure_ascii=False), flush=True)
    if not report['cases']:
        parser.error('No matching evaluation cases')
    report['summary'] = {'total': len(report['cases']),
                         'automatic_passes': sum(bool(c.get('checks')) and all(c['checks'].values()) for c in report['cases']),
                         'errors': sum('error' in c for c in report['cases']),
                         'median_seconds': statistics.median(c['seconds'] for c in report['cases']),
                         'total_seconds': sum(c['seconds'] for c in report['cases']),
                         'manual_review_required': True}
    save()
    print(json.dumps(report['summary']), flush=True)
    return int(report['summary']['errors'] > 0 or
               report['summary']['automatic_passes'] != report['summary']['total'])


if __name__ == '__main__':
    raise SystemExit(main())

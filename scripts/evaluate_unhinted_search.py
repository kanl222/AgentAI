"""Test natural questions without supplying expected sources to retrieval/model.

Run inside the API container. Reads the existing index; standalone generation
does not save chats. The HTTP conversation is created and removed by this run.
Expected source prefixes and fact fragments are evaluation data only.
"""
import json
import argparse
import os
from pathlib import Path
import re
import time

import httpx
from agentai.access import authenticate
from agentai.adapters.lmstudio import lmstudio_clients
from agentai.answering.engine import answer
from agentai.execution.capacity import Capacity
from agentai.knowledge.scoping import explicit_targets
from agentai.knowledge.store import Store


CASES = [
    ('overview', 'Что такое исло?', ['20119567_'], ['лекарствен', 'обеспечени']),
    ('account', 'Как добавить сотрудника из ЕЦП, если его ещё нет в программе?', ['1081353_'], ['СНИЛС', 'ОГРН']),
    ('wards', 'Как загрузить палаты из ЕЦП только для одного отделения?', ['1081494_'], ['ОГРН', 'lpu_section_id_ecp']),
    ('benefits', 'Чем наша система лекарственного обеспечения лучше ЕЦП?', ['328335378_'], ['отчёт']),
    ('documents', 'Какими документами оформляют выдачу лекарств пациенту и внутреннее списание?', ['53149701_'], ['ПотреблениеТоваровПоНазначениямВрачей', 'ВнутреннееПотреблениеТоваров']),
    ('document-tables', 'Какие таблицы хранят выдачу лекарств пациенту, списание БЭН и множественную выдачу?', ['53149701_'], ['doc_mo_delivery', 'doc_mo_dlvnoapnt', 'doc_mo_issue']),
    ('all-stores', 'Какое поле разрешает просмотр отчётов сразу по всем складам и местам хранения, и какого оно типа?', ['318275590_'], ['flag_all_store', 'int']),
    ('request-modes', 'Какие числовые значения задают вывод всех заявок, заявленных без выписки и выписанных без заявки?', ['318275590_'], ['flag_order', '1', '2', '3']),
    ('report-access', 'Где в коде задаётся каталог доступных отчётов и доступ к ним?', ['323223571_'], ['AvailableReports']),
    ('report-pipelines', 'Чем отличаются встроенные отчёты и отчёты SSRS по месту выполнения логики и контроллерам?', ['323223569_'], ['DefaultController', 'FormController']),
    ('new-report', 'Что нужно создать, чтобы добавить новый встроенный отчёт?', ['318275597_'], ['FormController', 'AvailableReports']),
    ('audit', 'Как посчитать количество изменений записей по дням, пользователям и IP-адресам?', ['269975556_'], ['modified_audit', 'modified_at', 'updater_username', 'updater_ip']),
    ('recipe', 'Как узнать, кто выписал рецепт, в какой медицинской организации и для какого пациента?', ['285278209_'], ['Written_recipe', 'C_OGRN']),
    ('missing-fact', 'Какой гарантированный SLA у боевого сервера и сколько у него оперативной памяти?', [], []),
]


def normalized(text):
    return text.casefold().replace('ё', 'е')


def assess(case, result):
    identity, question, prefixes, terms = case
    def rank(rows):
        return next((i for i, row in enumerate(rows, 1)
                     if any('/'+prefix in row['citation'] for prefix in prefixes)), None)
    body = result['answer'].split('\n\nSources:')[0].split('\n\nНайденные материалы')[0]
    checks = {'not_truncated': result.get('finish_reason') != 'length',
              'validation': result['validation']['passed']}
    if prefixes:
        checks.update(expected_source_found=rank(result['sources']) is not None,
                      expected_source_cited=rank(result['used_sources']) is not None,
                      expected_fact_terms=all(normalized(term) in normalized(body) for term in terms))
    else:
        checks['admits_missing_fact'] = bool(re.search(
            r'не (?:указан|содерж|найден|предоставлен|могу)|нет (?:данных|информации|сведений)|отсутств', body, re.I))
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('/tmp/unhinted-evaluation.json'))
    parser.add_argument('--case',action='append',dest='case_ids')
    parser.add_argument('--skip-conversation',action='store_true')
    parser.add_argument('--conversation-only',action='store_true')
    args=parser.parse_args()
    destination = args.output
    cases=[case for case in CASES if not args.case_ids or case[0] in args.case_ids]
    if not cases: parser.error('No matching cases')
    assert all(not any(explicit_targets(case[1])) for case in cases)
    user = authenticate('Bearer '+os.environ['WEBUI_READER_KEY'])
    embeddings, model = lmstudio_clients()
    store, capacity = Store(embeddings), Capacity()
    report = {'model': os.getenv('CHAT_MODEL'), 'mode': 'existing_index_no_source_hints',
              'retrieval': [], 'standalone': [], 'conversation': [], 'checks': {}}

    def save():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for identity, question, prefixes, terms in cases:
        rows = store.search(question, user['sources'])
        rank = next((i for i,r in enumerate(rows,1) if any('/'+p in r['citation'] for p in prefixes)),None)
        report['retrieval'].append(dict(id=identity, question=question, expected=prefixes, rank=rank, rows=rows))
    save()
    print(json.dumps({'phase':'retrieval','positive':sum(bool(case[2]) for case in cases),
                      'found':sum(r['rank'] is not None for r in report['retrieval'] if r['expected'])}),flush=True)

    for case in ([] if args.conversation_only else cases):
        if not capacity.acquire(blocking=False): raise RuntimeError('GPU busy; retry later')
        started = time.monotonic()
        record = {'id':case[0], 'question':case[1]}
        try:
            result = answer(case[1], user, store, model)
            record.update(result)
            record['checks'] = assess(case, result)
        except Exception as error:
            record['error'] = type(error).__name__
        finally:
            capacity.release()
        record['seconds'] = round(time.monotonic()-started, 2)
        report['standalone'].append(record); save()
        print(json.dumps({'phase':'standalone','id':case[0],'checks':record.get('checks'),
                          'error':record.get('error'),'seconds':record['seconds']}),flush=True)

    # Exercise the same queue and persisted history used by the web chat.
    if not args.skip_conversation:
        with httpx.Client(base_url='http://127.0.0.1:8000',timeout=200,
                          headers={'Authorization':'Bearer '+os.environ['WEBUI_READER_KEY']}) as client:
            response = client.post('/conversations'); response.raise_for_status()
            conversation = response.json()['id']
            try:
                for index in [4, 1, 9, 2]:
                    case = CASES[index]
                    response=client.post('/jobs/chat',json={'question':case[1],'conversation_id':conversation})
                    response.raise_for_status(); job=response.json(); deadline=time.monotonic()+190
                    while job['state'] in ('queued','running') and time.monotonic()<deadline:
                        time.sleep(.5)
                        response=client.get('/jobs/'+job['id']); response.raise_for_status(); job=response.json()
                    if job['state']!='succeeded': raise RuntimeError('Test job: '+job['state'])
                    result=job['result']; checks=assess(case,result)
                    report['conversation'].append(dict(id=case[0],question=case[1],checks=checks,**result)); save()
                    print(json.dumps({'phase':'conversation','id':case[0],'checks':checks}),flush=True)
                turns=client.get('/conversations/'+conversation).json()['turns']
                report['checks']['four_turns'] = len(turns)==4
                report['checks']['different_answers'] = len({turn['answer'] for turn in turns})==4
            finally:
                response=client.delete('/conversations/'+conversation); response.raise_for_status()
                report['checks']['test_conversation_removed'] = client.get('/conversations/'+conversation).status_code==404
                save()
    report['summary'] = {
        'retrieval_positive':sum(bool(case[2]) for case in cases),
        'retrieval_found':sum(r['rank'] is not None for r in report['retrieval'] if r['expected']),
        'standalone_cases':len(report['standalone']),
        'standalone_automatic_passes':sum(bool(r.get('checks')) and all(r['checks'].values()) for r in report['standalone']),
        'conversation_cases':len(report['conversation']),
        'conversation_automatic_passes':sum(all(r['checks'].values()) for r in report['conversation']),
        'manual_review_required':True,
    }
    save(); print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':
    main()

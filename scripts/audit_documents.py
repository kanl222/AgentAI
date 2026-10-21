"""Audit a local PDF corpus and optionally probe an existing AgentAI instance.

Does not upload documents or change the running index. Live checks test source
selection, citations, selected factual mappings and SQL parameterization. They
are regression checks, not a general proof of factual correctness or SQL execution.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentai.retrieval import scope_records
from agentai.validation import relation_values


CASE_DEFINITIONS = [
    ('53149701_', 'Типы документов в ИС «ЛО»', ['ПотреблениеТоваровПоНазначениямВрачей', 'ВнутреннееПотреблениеТоваров']),
    ('53149701_', 'Каким трём таблицам соответствует ПотреблениеТоваровПоНазначениямВрачей?', ['doc_mo_delivery', 'doc_mo_dlvnoapnt', 'doc_mo_issue']),
    ('318275590_', 'Что означает flag_all_store? Приведи точное имя поля и тип.', ['int', 'склад']),
    ('318275590_', 'Какие значения принимает flag_order?', ['1', '2', '3']),
    ('323223571_', 'Какой файл отвечает за каталог и доступ? Приведи его путь.', ['AvailableReports.php']),
    ('323223569_', 'Сравни report_type для SSRS и ИСЛО и соответствующие контроллеры.', ['DefaultController', 'FormController', '1', '2']),
    ('269975556_', 'Напиши SQL для подсчёта изменений объектов по дате, пользователю и IP. Сохрани имена таблицы и полей из документа.', ['modified_audit', 'modified_at', 'updater_username', 'updater_ip', 'GROUP BY']),
    ('285278209_', 'Напиши SQL: когда, кто, из какой МО и кому выписал рецепт. Используй таблицы и JOIN из документа; номер рецепта обозначь параметром.', ['Written_recipe', 'C_OGRN', 'sn_lr', 'JOIN']),
]


def audit(folder):
    from agentai.pdf import pages as pdf_pages
    inventory, rows = [], []
    for path in sorted(folder.glob('*.pdf')):
        data = path.read_bytes()
        pages = list(pdf_pages(data))
        inventory.append({
            'filename': path.name, 'sha256': hashlib.sha256(data).hexdigest(),
            'pages': len(pages), 'characters': sum(map(len, pages)),
            'empty_pages': [i + 1 for i, text in enumerate(pages) if not text.strip()],
            'wrapped_identifiers': sorted(set(re.findall(r'\b[A-Za-z]\w*_\s*\n\s*[a-z]+\b', '\n'.join(pages)))),
            'sql_empty_aliases': sum(len(re.findall(r'\bAS\s+[\"\']\s*[\"\']', text, re.I)) for text in pages),
        })
        for number, text in enumerate(pages, 1):
            if text.strip():
                rows.append({'id': f'{path.name}:{number}', 'source': 'audit',
                             'text': text, 'citation': f'document:audit/{path.name}?page={number}'})
    checks = []
    for item in inventory:
        selected, targeted = scope_records(rows, 'Прочитай ' + item['filename'])
        checks.append({'kind': 'exact_filename', 'query': item['filename'],
                       'passed': targeted and len(selected) == item['pages'] - len(item['empty_pages'])
                       and all(item['filename'] in row['citation'] for row in selected)})
    probes = [
        ('Что означает flag_all_store?', '318275590_'),
        ('Как работает flag_order?', '318275590_'),
        ('Где находится AvailableReports.php?', '323223571_'),
        ('Где находится _param_form.php?', '323223571_'),
        ('Покажи отсутствующий_файл_проверки.php', None),
    ]
    for query, expected in probes:
        selected, targeted = scope_records(rows, query)
        checks.append({'kind': 'identifier_scope', 'query': query,
                       'passed': targeted and (any(expected in r['citation'] for r in selected) if expected else not selected),
                       'citations': [r['citation'] for r in selected]})
    return {'created_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'offline_extraction_and_scope', 'inventory': inventory,
            'summary': {'documents': len(inventory), 'pages': sum(i['pages'] for i in inventory),
                        'characters': sum(i['characters'] for i in inventory),
                        'empty_pages': sum(len(i['empty_pages']) for i in inventory),
                        'checks_passed': sum(c['passed'] for c in checks), 'checks_total': len(checks)},
            'checks': checks}


def table_mapping(answer):
    """Every explicit assignment must agree; a correct sentence cannot mask its opposite."""
    labels = ['SSRS', 'ИСЛО']
    controllers = relation_values(answer, labels, ['DefaultController', 'FormController'])
    types = relation_values(answer, labels, ['1', '2'])
    return (controllers == {'SSRS': {'DefaultController'}, 'ИСЛО': {'FormController'}}
            and types == {'SSRS': {'1'}, 'ИСЛО': {'2'}})


def case_checks(prefix, filename, response, terms):
    body = response['answer'].split('\n\nSources:')[0].split('\n\nНайденные материалы')[0]
    prose = re.sub(r'```[\s\S]*?```', '', body)
    absence = re.compile(r'нет (?:данных|доказательств)|\bне\s+(?:найден[аоы]?|указан[аоы]?)\b|'
                         r'отсутству[ею]т (?:данные|информация)', re.I)
    checks = {
        'expected_source': any(filename in r['citation'] for r in response.get('sources', [])),
        'used_expected_source': any(filename in r['citation'] for r in response.get('used_sources', [])),
        'expected_terms': all(t.lower() in body.lower() for t in terms),
        'no_false_absence_phrase': not bool(absence.search(prose)),
        'not_truncated': response.get('finish_reason') != 'length',
        'no_unverified_links': not re.search(r'https?://|ссылка не подтверждена|источник не подтверждён', prose),
    }
    if prefix == '323223569_':
        checks['correct_table_mapping'] = table_mapping(prose)
    if prefix == '285278209_':
        conditions = re.findall(r'\b(?:wr\.)?sn_lr\s*=\s*([^\s;]+)', body, re.I)
        checks['parameterized_recipe'] = bool(conditions) and all(
            re.fullmatch(r'@[a-zA-Z_]\w*|:[a-zA-Z_]\w*|\?|%s', value) for value in conditions)
    if prefix in {'269975556_', '285278209_'}:
        code = re.findall(r'```[^\n]*\n([\s\S]*?)```', body)
        checks['sql_code_separated_from_sources'] = bool(code) and any(
            re.search(r'\bSELECT\b', block, re.I) for block in code) and not re.search(
            r'^\s*(?:SELECT|WITH|FROM|WHERE|JOIN|GROUP BY|ORDER BY)\b', prose, re.I | re.M) and all(
            not re.search(r'document:|\[[SСC][0-9]+\]', block) for block in code)
    if 'validation' in response:
        checks['answer_validation'] = response['validation']['passed']
    return checks


def live_probe(base, key, source, result, save):
    def call(path, body=None):
        request = Request(base.rstrip('/') + path,
                          data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=600) as response:
            return json.load(response)

    versions, offset = [], 0
    while True:
        page = call(f'/documents?limit=200&offset={offset}')['versions']
        versions.extend(page)
        if len(page) < 200:
            break
        offset += 200
    indexed = {v['sha256'] for v in versions if v['source'] == source and v['state'] == 'indexed'}
    result['index_coverage'] = [{'filename': i['filename'], 'indexed': i['sha256'] in indexed} for i in result['inventory']]
    result['live_cases'] = []
    for prefix, question, terms in CASE_DEFINITIONS:
        filename = next((i['filename'] for i in result['inventory'] if i['filename'].startswith(prefix)), None)
        if not filename:
            continue
        # Exact-title request is kept as the regression for the original incident.
        query = question if question == 'Типы документов в ИС «ЛО»' else question + '\nИспользуй документ ' + filename
        case = {'question': query, 'expected_filename': filename}
        try:
            response = call('/chat', {'question': query})
            case.update(response)
            case['checks'] = case_checks(prefix, filename, response, terms)
        except Exception as error:
            case['error'] = type(error).__name__
        result['live_cases'].append(case)
        save()
        print('Live case:', prefix, case.get('checks', case.get('error')), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path, default=Path('work/cf-audit/results.json'))
    parser.add_argument('--live-url', help='Optional existing AgentAI URL, e.g. http://localhost:8000')
    parser.add_argument('--env', type=Path, help='Read WEBUI_READER_KEY from this local .env; never print it')
    parser.add_argument('--source', default='docs-demo')
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error('Expected an existing PDF folder')
    result = audit(args.folder)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    if args.live_url:
        import os
        key = os.getenv('AGENTAI_READER_KEY', '')
        if args.env:
            from setup_documents import read_env
            _, values = read_env(args.env)
            key = values.get('WEBUI_READER_KEY', '')
        if not key:
            parser.error('Set AGENTAI_READER_KEY or provide --env with WEBUI_READER_KEY')
        try:
            live_probe(args.live_url, key, args.source, result, save)
        except Exception as error:
            result['live_error'] = type(error).__name__
            save()
            print('Live probe unavailable:', type(error).__name__, file=sys.stderr)
            return 2
    print(json.dumps(result['summary'], ensure_ascii=False))
    return 0 if result['inventory'] and all(c['passed'] for c in result['checks']) and not any(
        c.get('error') or not all(c.get('checks', {}).values()) for c in result.get('live_cases', [])
    ) and all(i['indexed'] for i in result.get('index_coverage', [])) else 1


if __name__ == '__main__':
    raise SystemExit(main())

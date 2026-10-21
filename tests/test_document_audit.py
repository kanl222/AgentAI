import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('document_audit',
    Path(__file__).resolve().parents[1] / 'scripts' / 'audit_documents.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def result(answer):
    citation = 'document:docs/comparison.pdf?page=1#L1-L4'
    row = {'citation': citation}
    return {'answer': answer, 'sources': [row], 'used_sources': [row], 'finish_reason': 'stop'}


def test_audit_rejects_controller_mixup_despite_all_expected_words():
    checks = audit.case_checks('323223569_', 'comparison.pdf',
        result('SSRS: report_type=1; контроллер не указан.\n'
               'ИСЛО: report_type=2; DefaultController и FormController.'),
        ['DefaultController', 'FormController', '1', '2'])
    assert checks['expected_terms']
    assert not checks['correct_table_mapping']


def test_audit_accepts_correct_markdown_table_and_prose_mapping():
    for text in (
        '| Система | report_type | Контроллер |\n|---|---|---|\n'
        '| SSRS | 1 | DefaultController |\n| ИСЛО | 2 | FormController |',
        'SSRS: report_type=1; DefaultController.\nИСЛО: report_type=2; FormController.',
        '- **SSRS**: `report_type` равен **1**.\n  Контроллер — **DefaultController**.\n\n'
        '- **ИСЛО**: `report_type` равен **2**.\n  Контроллер — **FormController**.',
        'Для report_type у SSRS значение равно 1, а у ИСЛО — 2.\n\n'
        '- Для SSRS: `DefaultController`\n- Для ИСЛО: `FormController`',
    ):
        checks = audit.case_checks('323223569_', 'comparison.pdf', result(text),
                                   ['DefaultController', 'FormController', '1', '2'])
        assert checks['correct_table_mapping']


def test_audit_rejects_swapped_types_and_a_correct_fact_followed_by_its_opposite():
    for text in (
        'SSRS: report_type=2; DefaultController.\nИСЛО: report_type=1; FormController.',
        'SSRS: report_type=1; DefaultController.\nИСЛО: report_type=2; FormController.\n\n'
        'SSRS: FormController. ИСЛО: DefaultController.',
    ):
        checks = audit.case_checks('323223569_', 'comparison.pdf', result(text),
                                   ['DefaultController', 'FormController', '1', '2'])
        assert not checks['correct_table_mapping']


def test_audit_rejects_false_absence_before_a_correct_enumeration():
    checks = audit.case_checks('318275590_', 'comparison.pdf',
        result('В документе не указано значение flag_order. Однако: 1, 2, 3.'), ['1', '2', '3'])
    assert not checks['no_false_absence_phrase']


def test_audit_requires_a_used_source_and_a_parameter_in_recipe_sql():
    response = result('```sql\nSELECT * FROM Written_recipe wr JOIN lpu l '
                      'ON l.C_OGRN=wr.C_OGRN WHERE wr.sn_lr=\'123\';\n```')
    response['used_sources'] = []
    checks = audit.case_checks('285278209_', 'comparison.pdf', response,
                               ['Written_recipe', 'C_OGRN', 'sn_lr', 'JOIN'])
    assert not checks['used_expected_source']
    assert not checks['parameterized_recipe']


def test_audit_rejects_citation_inserted_into_unfenced_sql():
    response = result('SELECT * FROM Written_recipe wr JOIN lpu l ON l.C_OGRN=wr.C_OGRN '
                      'WHERE wr.sn_lr = ? document:docs/comparison.pdf?page=1#L1-L4')
    checks = audit.case_checks('285278209_', 'comparison.pdf', response,
                               ['Written_recipe', 'C_OGRN', 'sn_lr', 'JOIN'])
    assert not checks['sql_code_separated_from_sources']

from agentai.answering.engine import answer
from test_answer_validation import Model, Store, USER


def test_general_knowledge_is_allowed_with_unrelated_retrieved_material():
    model = Model(['Из общих знаний: кэш хранит результаты вычислений для повторного использования.'])
    result = answer('Объясни принцип кэширования', USER, Store('Инструкция по оформлению заявки.'), model)
    assert len(model.calls) == 1
    policy = model.calls[0][0].content
    assert 'собственные общие знания' in policy
    assert 'Дополняй ответ общими знаниями' in policy
    assert 'Если доказательств нет, скажи об этом и запроси уточнение' not in policy
    assert 'У каждого фактического вывода ставь метку' not in policy
    assert result['answer'].startswith('Из общих знаний: кэш')
    assert result['used_sources'] == []
    assert result['validation']['passed']
    assert not result['validation']['correction_attempted']


def test_empty_index_does_not_force_a_refusal():
    class EmptyStore:
        def search(self, *args):
            return []

    model = Model(['Из общих знаний: кэш уменьшает повторную работу.'])
    result = answer('Объясни принцип кэширования', USER, EmptyStore(), model)
    assert len(model.calls) == 1
    assert result['answer'] == 'Из общих знаний: кэш уменьшает повторную работу.'
    assert result['sources'] == result['used_sources'] == []
    assert result['validation']['passed']


def test_correction_allows_general_knowledge_without_fabricated_citations():
    model = Model(['Кэш ускоряет повторные обращения. [S99]',
                   'Из общих знаний: кэш ускоряет повторные обращения.'])
    result = answer('Объясни принцип кэширования', USER, Store('Инструкция по оформлению заявки.'), model)
    assert len(model.calls) == 2
    correction = model.calls[1][-1].content
    assert 'Ответ можно дополнять общими знаниями' in correction
    assert 'Не создавай адресов и новых фактов' not in correction
    assert result['used_sources'] == []
    assert result['validation']['passed']


def test_validity_metadata_does_not_add_age_disclaimers_to_prompt_or_answer():
    class MixedValidityStore:
        def search(self, *args):
            return [
                {'id': 'old', 'source': 'docs', 'text': 'Инструкция по настройке кэша. Не актуально!',
                 'citation': 'document:docs/cache.pdf?page=1', 'validity': 'obsolete'},
                {'id': 'new', 'source': 'docs', 'text': 'Пример параметров кэша.',
                 'citation': 'document:docs/example.pdf?page=1', 'validity': 'current'},
            ]

    model = Model(['Кэш сохраняет результаты вычислений. [S1]'])
    result = answer('Объясни принцип кэширования', USER, MixedValidityStore(), model)
    policy = model.calls[0][0].content
    assert 'Устаревшие документы используй с явной оговоркой' not in policy
    assert 'Проверяй validity и note' not in policy
    assert 'устаревшие документы' not in result['answer']
    assert 'статус актуальности' not in result['answer']
    assert result['warnings'] == []
    assert {row['validity'] for row in result['sources']} == {'obsolete', 'current'}

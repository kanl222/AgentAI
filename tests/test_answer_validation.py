from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agentai.agent import answer


USER = {'roles': ['reader'], 'sources': ['docs']}
TEXT = ('Таблица 1, строка 24: Поле: flag_order | Тип: int | '
        'Комментарий: Всех = 1; Заявлены, но не выписаны = 2; Выписаны, но не заявлены = 3')


class Store:
    def __init__(self, text=TEXT):
        self.text = text

    def search(self, *args):
        return [{'id': 'fields', 'source': 'docs', 'text': self.text,
                 'citation': 'document:docs/fields.pdf?page=1#L1-L4'}]


class Model:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=self.responses[min(len(self.calls)-1, len(self.responses)-1)])


def test_contradiction_is_corrected_once_with_specific_feedback_and_source():
    model = Model(['Значения flag_order не указаны. Допустимы значения: 1, 2, 3. [S1]',
                   'flag_order: Всех = 1; Заявлены, но не выписаны = 2; Выписаны, но не заявлены = 3. [S1]'])
    result = answer('Какие значения принимает flag_order?', USER, Store(), model)
    assert len(model.calls) == 2
    correction = model.calls[1][-1]
    assert isinstance(correction, HumanMessage)
    assert 'противореч' in correction.content.lower()
    assert any(isinstance(message,ToolMessage) and 'Заявлены, но не выписаны = 2' in message.content
               for message in model.calls[1])
    assert result['validation']['passed']
    assert result['validation']['correction_attempted']
    assert not result['validation']['issues']


def test_second_pass_is_checked_and_not_retried_indefinitely():
    model = Model(['Значения flag_order не указаны. Значения: 1, 2, 3. [S1]'])
    result = answer('Какие значения принимает flag_order?', USER, Store(), model, max_steps=8)
    assert len(model.calls) == 2
    assert not result['validation']['passed']
    assert any(i['code'] == 'contradictory_absence' for i in result['validation']['issues'])
    assert 'не прошёл проверку' in result['answer'].lower()


def test_honest_absence_and_unrelated_numbered_instructions_are_not_a_contradiction():
    model = Model(['Срок SLA не указан. Порядок обращения:\n1. Напишите заявку.\n2. Укажите контакт. [S1]'])
    result = answer('Какой SLA поддержки?', USER, Store('Инструкция: обращайтесь в поддержку.'), model)
    assert len(model.calls) == 1
    assert result['validation']['passed']


def test_contradiction_check_does_not_parse_a_quoted_example_as_own_claim():
    model = Model(['Пример ошибочного ответа:\n> Значения не указаны. Значения: 1, 2, 3.\n'
                   'Верные значения flag_order: 1, 2, 3. [S1]'])
    result = answer('Объясни значения flag_order', USER, Store(), model)
    assert len(model.calls) == 1
    assert result['validation']['passed']


def test_swapped_table_values_trigger_a_source_based_correction():
    text = ('Таблица 1, строка 7: Параметр: Контроллер | SSRS: DefaultController | ИСЛО: FormController')
    model = Model(['SSRS: FormController. ИСЛО: DefaultController. [S1]',
                   'SSRS: DefaultController. ИСЛО: FormController. [S1]'])
    result = answer('Сравни контроллеры SSRS и ИСЛО', USER, Store(text), model)
    assert len(model.calls) == 2
    assert any(isinstance(message,ToolMessage) and 'SSRS: DefaultController' in message.content
               for message in model.calls[1])
    assert result['validation']['passed']


def test_validation_respects_one_step_budget():
    model = Model(['Значения flag_order не указаны. Значения: 1, 2, 3. [S1]'])
    result = answer('Какие значения принимает flag_order?', USER, Store(), model, max_steps=1)
    assert len(model.calls) == 1
    assert not result['validation']['passed']
    assert not result['validation']['correction_attempted']


def test_second_pass_can_introduce_a_new_contradiction_and_is_rejected():
    model = Model(['Значения flag_order: 1, 2, 3. [S99]',
                   'Значения flag_order не указаны. Значения: 1, 2, 3. [S1]'])
    result = answer('Какие значения принимает flag_order?', USER, Store(), model)
    assert len(model.calls) == 2
    assert not result['validation']['passed']
    assert any(i['code'] == 'contradictory_absence' for i in result['validation']['issues'])

from langchain_core.messages import AIMessage,HumanMessage

from agentai.answering.engine import answer
from agentai.answering.session import AnswerSession
from agentai.answering.code_checks import normalize_sql_aliases


USER = {'roles':['reader'], 'sources':['docs']}
LOCATION = 'document:docs/orders.md@v1#L1-L20'


class Store:
    def __init__(self, text='Order source', location=LOCATION):
        self.text, self.location = text, location

    def search(self, query, sources):
        return [{'id':'one','source':'docs','text':self.text,'citation':self.location}]


class Model:
    def __init__(self, text): self.text, self.messages = text, []
    def bind_tools(self, tools): return self
    def invoke(self, messages):
        self.messages.append(messages)
        return AIMessage(content=self.text)


def test_long_followup_retains_source_and_search_target():
    history = [{'question':'Проверь orders.md', 'answer':'answer',
                'context':{'selected':[['docs','document:docs/orders.md@v1']]}}]
    question = 'Подробнее: как при этом сортируются клиенты с равными суммами? Ответь кратко со ссылкой.'
    session = AnswerSession(question, USER, Store(), history)
    assert session.scope_ready
    assert 'orders.md' in session.query


def test_explicit_new_document_or_topic_does_not_keep_previous_scope():
    history = [{'question':'orders.md', 'answer':'answer',
                'context':{'selected':[['docs','document:docs/orders.md@v1']]}}]
    for question in ('Подробнее расскажи о other.md', 'Другая тема: какие отчеты доступны?'):
        assert not AnswerSession(question, USER, Store(), history).scope_ready


def test_third_turn_keeps_target_when_previous_question_has_no_filename():
    history = [{'question':'Какой порядок при равных суммах?', 'answer':'answer',
                'context':{'selected':[['docs','document:docs/orders.md@v1']]}}]
    session = AnswerSession('Почему выбран такой порядок?',USER,Store(),history)
    assert session.scope_ready and 'orders.md' in session.query


def test_code_skill_is_injected_before_the_first_generation():
    model = Model('Исходный код [S1]')
    answer('Проанализируй функцию из orders.md',USER,Store('def total(items):\n    return sorted(items)'),model)
    assert 'стабильност' in model.messages[0][0].content.lower()


def test_duplicate_sources_footer_is_removed_before_backend_footer():
    result = answer('Как работает код?',USER,Store(),Model('Answer [S1]\n\nSources:\n- [S1]'))
    assert result['answer'].count('Sources:') == 1


def test_sql_does_not_invent_column_meaning_from_blank_alias():
    result = answer('Напиши SQL из документа',USER,
                    Store("SELECT wr.ss AS ' ' FROM Written_recipe wr"),
                    Model("```sql\nSELECT wr.ss AS 'Специальность' FROM Written_recipe wr\n```\n[S1]"))
    assert result['validation']['passed']
    assert 'Специальность' not in result['answer']
    assert 'wr.ss' in result['answer']


def test_code_proposal_cannot_silently_add_a_tie_breaker():
    text = "```python\ndef totals(rows):\n    return sorted(rows, key=lambda r: r['total'], reverse=True)\n```"
    result = answer('Улучши totals, сохрани поведение',USER,Store(text),
                    Model("```python\ndef totals(rows):\n    return sorted(rows, key=lambda r: (-r['total'], r['id']))\n```\n[S1]"))
    assert not result['validation']['passed']
    assert any(i['code']=='sort_contract' for i in result['validation']['issues'])


def test_existing_multi_key_sort_and_documented_sql_label_are_allowed():
    text = "```python\ndef totals(rows):\n    return sorted(rows, key=lambda r: (r['total'], r['id']))\n```"
    assert answer('Улучши totals, сохрани поведение',USER,Store(text),Model(text+'\n[S1]'))['validation']['passed']
    text = "SELECT wr.ss AS 'Идентификатор' FROM Written_recipe wr"
    response = "```sql\n"+text+"\n```\n[S1]"
    assert answer('Напиши SQL из документа',USER,Store(text),Model(response))['validation']['passed']


def test_a_self_test_outside_testcase_is_rejected():
    response = '```python\ndef test_total(self):\n    self.assertEqual(1,1)\n```\n[S1]'
    result = answer('Предложи тесты метода',USER,Store(),Model(response))
    assert any(i['code']=='incomplete_test' for i in result['validation']['issues'])


def test_missing_explicit_file_is_reported_without_model_guessing():
    class Empty:
        def search(self,*args): return []
    model = Model('Файл существует')
    result = answer('Прочитай missing.pdf',USER,Empty(),model)
    assert not model.messages
    assert 'не найден' in result['answer'] and not result['used_sources']


def test_sql_alias_normalization_preserves_dependent_aliases_and_literal_text():
    rows=Store("SELECT wr.ss AS ' ' FROM Written_recipe wr").search('',[])
    for text in (
        '```sql\nSELECT wr.ss AS "meaning" FROM t ORDER BY "meaning"\n```',
        '```sql\nSELECT wr.ss AS "meaning" FROM t ORDER BY meaning\n```',
        '```sql\nSELECT \'wr.ss AS "meaning"\' FROM t\n```',
        '```sql\n-- wr.ss AS "meaning"\nSELECT 1\n```',
    ):
        assert normalize_sql_aliases(text,rows,'SQL') == text
    assert normalize_sql_aliases('```sql\nSELECT wr.ss AS "meaning" FROM t\n```',rows,'SQL') == '```sql\nSELECT wr.ss  FROM t\n```'


def test_complexity_cannot_hide_missing_nested_cost_with_an_unrelated_items_mention():
    original='```python\ndef totals(orders):\n    for order in orders:\n        total=sum(item for item in order["items"])\n    return sorted(orders)\n```'
    result=answer('Оцени сложность функции',USER,Store(original),
                  Model('Сложность O(N + U log U).\n\nРезультат использует totals.items(). [S1]'))
    assert any(i['code']=='nested_cost' for i in result['validation']['issues'])


def test_explicit_stability_request_restores_source_sort_key():
    original="```python\ndef totals(rows):\n    return sorted(rows,key=lambda r:r['total'],reverse=True)\n```"
    proposed="```python\ndef totals(rows):\n    return sorted(rows,key=lambda row:(-row['total'],row['id']))\n```\n[S1]"
    result=answer('Сохрани стабильный порядок равных итогов',USER,Store(original),Model(proposed))
    assert result['validation']['passed']
    assert "row['id']" not in result['answer']
    assert "r['total']" in result['answer']


def test_repair_does_not_repeat_the_rejected_answer_as_an_example():
    class Repairs(Model):
        def invoke(self,messages):
            self.messages.append(messages)
            if len(self.messages)==1:
                return AIMessage(content='REJECTED_ANSWER_WITH_NO_CITATION')
            assert not any('REJECTED_ANSWER_WITH_NO_CITATION' in str(message.content) for message in messages)
            return AIMessage(content='Answer [S1]')
    model=Repairs('')
    assert answer('Вопрос',USER,Store(),model)['validation']['passed']


def test_review_feedback_survives_a_tool_call_during_repair():
    class Repairs(Model):
        def invoke(self,messages):
            self.messages.append(messages)
            if len(self.messages)==1: return AIMessage(content='No citation')
            if len(self.messages)==2:
                return AIMessage(content='',tool_calls=[{'id':'skill','name':'load_skill','args':{'name':'documents'}}])
            assert any(isinstance(m,HumanMessage) and 'Отсутствует точная ссылка' in m.content for m in messages)
            return AIMessage(content='Answer [S1]')
    assert answer('Вопрос',USER,Store(),Repairs(''))['validation']['passed']


def test_analysis_only_removes_a_copied_method_and_stray_fence():
    text='```python\ndef method(): pass\n```\n```\nОписание [S1]'
    result=answer('Только анализ, не переписывай код',USER,Store(),Model(text))
    assert 'def method' not in result['answer'] and '```' not in result['answer']
    assert result['used_sources'] and result['validation']['passed']

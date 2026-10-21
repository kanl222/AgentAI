"""A new question must reach retrieval without the preceding topic's scope."""
import pytest

from agentai.answering.session import AnswerSession


USER = {'roles': ['reader'], 'sources': ['docs']}


class TopicStore:
    def __init__(self):
        self.queries = []

    def search(self, query, sources):
        self.queries.append(query)
        return [dict(id='new', source='docs', citation='document:docs/reports.pdf@v2#L1',
                     text='Отчёты открывает FormController.')]


@pytest.mark.parametrize('selected', [[], [['docs', 'document:docs/orders.md@v1']]])
@pytest.mark.parametrize('question', ['Какие отчёты доступны?', 'Что такое ИСЛО?',
                                   'Как настроить права пользователей?'])
def test_new_subject_searches_current_question_and_returns_new_document(selected, question):
    store = TopicStore()
    history = [dict(question='Проанализируй orders.md', answer='Предыдущий ответ о заказах.',
                    context={'selected': selected})]
    session = AnswerSession(question, USER, store, history)
    session.retrieve()
    assert store.queries == [question]
    assert [row['id'] for row in session.rows] == ['new']
    assert not session.scope_ready


def test_explicit_topic_switch_does_not_mix_previous_question():
    question = 'Новый вопрос: как настроить права?'
    session = AnswerSession(question, USER, TopicStore(), [dict(
        question='Проанализируй orders.md', answer='Ответ', context={'selected': []})])
    assert session.query == question


def test_followup_without_selected_documents_does_not_reintroduce_old_filename():
    session = AnswerSession('Расскажи подробнее', USER, TopicStore(), [dict(
        question='Где missing.pdf?', answer='Не найден', context={'selected': []})])
    assert 'missing.pdf' not in session.query

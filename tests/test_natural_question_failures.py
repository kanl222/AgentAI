"""Regression signals from natural questions, with no source hints."""
from agentai.answering.session import AnswerSession
from agentai.answering.validation import inspect_answer


def test_pronoun_with_its_own_noun_does_not_pin_previous_subject():
    question='Как добавить сотрудника из ЕЦП, если его ещё нет в программе?'
    history=[{'question':'Какими документами оформляют выдачу лекарств?', 'answer':'Выдача',
              'context':{'selected':[['docs','document:docs/types.pdf@v1']]}}]
    session=AnswerSession(question,{'roles':['reader'],'sources':['docs']},None,history)
    assert session.query==question
    assert not session.scope_ready


def test_natural_value_question_catches_denial_followed_by_explicit_values():
    rows=[dict(id='field',source='docs',citation='fields.pdf#L1',text=(
        'Поле: flag_order | Тип: int | Комментарий: Всех = 1; '
        'Заявлены, но не выписаны = 2; Выписаны, но не заявлены = 3'))]
    question='Какие числовые значения задают вывод всех заявок?'
    text=('В документах не найдено числовых значений для вывода заявок. '
          'В таблице указано: Всех = 1; Заявлены, но не выписаны = 2; Выписаны, но не заявлены = 3. [S1]')
    assert any(i['code']=='contradictory_absence' for i in inspect_answer(question,text,rows))


def test_compact_term_denial_is_checked_against_source_definition():
    rows=[dict(id='definition',source='docs',citation='description.pdf#L1',
               text='АБ «ВГ»\nОбщее описание информационной системы\nНаименование: Система учёта.')]
    issues=inspect_answer('Что такое абвг?', 'Информации не найдено. Данных нет. [S1]',rows)
    assert any(i['code']=='unsupported_absence' for i in issues)
    assert not inspect_answer('Что такое неизвестное?', 'Данных нет. [S1]',rows)


def test_howto_denial_cannot_precede_the_same_answer_as_numbered_steps():
    rows=[dict(id='instructions',source='docs',citation='instructions.pdf#L1',text='Пошаговое руководство\nДобавление пользователя.')]
    text=('Документы не содержат прямых инструкций. Однако описан процесс добавления:\n'
          '1. Введите СНИЛС.\n2. Введите ОГРН. [S1]')
    assert any(i['code']=='contradictory_absence' for i in inspect_answer('Как добавить пользователя?',text,rows))


def test_direct_denial_of_codes_is_distinct_from_missing_record_counts():
    rows=[dict(id='field',source='docs',citation='fields.pdf#L1',text='Всех = 1; Заявлены = 2; Выписаны = 3')]
    text='Нет прямых числовых значений. Значения: Все = 1; Заявлены = 2; Выписаны = 3.'
    assert any(i['code']=='contradictory_absence' for i in inspect_answer('Какие числовые значения задают режим вывода?',text,rows))
    assert not inspect_answer('Сколько записей в каждой категории?',text,rows)

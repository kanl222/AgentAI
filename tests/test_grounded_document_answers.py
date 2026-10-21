from langchain_core.messages import AIMessage

from agentai.answering.validation import inspect_answer
from agentai.answering.engine import answer


GROUP=('Операции\n1) Выдача на пациента\n2) Списание БЭН\n3) Множественная выдача\n'
       'Для нашего понимания это то что в табл\n"movementDrug".doc_mo_delivery\n'
       '"movementDrug".doc_mo_dlvnoapnt\n"movementDrug".doc_mo_issue')
ROW=dict(id='types',source='docs',citation='document:docs/types.pdf#L1-L10',text=GROUP)


def test_two_source_lists_do_not_prove_an_arbitrary_pair():
    issues=inspect_answer('Какие таблицы хранят выдачу и списание?',
                          'Выдача лекарств пациенту: movementDrug.doc_mo_issue. [S1]',[ROW])
    assert any(i['code']=='ambiguous_list_mapping' for i in issues)


def test_grouped_tables_and_explicitly_documented_pairs_are_allowed():
    response='Для перечисленных операций названы таблицы doc_mo_delivery, doc_mo_dlvnoapnt, doc_mo_issue. [S1]'
    assert not inspect_answer('Какие таблицы?',response,[ROW])
    explicit=dict(ROW,id='pair',text='Выдача на пациента: doc_mo_issue.')
    assert not inspect_answer('Какие таблицы?', 'Выдача на пациента: doc_mo_issue. [S1]',[ROW,explicit])


def test_repeated_unproved_mapping_returns_actual_source_instead():
    class Store:
        def search(self,*args):return [dict(ROW,text=GROUP+'\nВнутреннееПотреблениеТоваров\nПорча ЛС\ndoc_mo_offs')]
    class Model:
        calls=0
        def bind_tools(self,tools):return self
        def invoke(self,messages):
            self.calls+=1
            return AIMessage(content='Выдача лекарств пациенту: movementDrug.doc_mo_issue. [S1]')
    model=Model()
    result=answer('Какие таблицы хранят выдачу?',{'roles':['reader'],'sources':['docs']},Store(),model)
    assert model.calls==2
    assert result['retrieval']['extractive_fallback']
    assert result['validation']['passed']
    assert '> 1) Выдача на пациента' in result['answer']
    assert '> ВнутреннееПотреблениеТоваров' in result['answer']
    assert 'отдельное соответствие каждой операции не установлено' in result['answer']


def test_comparison_denial_cannot_hide_an_existing_comparison():
    rows=[dict(ROW,text='Плюсы АБ по сравнению с ВГ\nГотовые отчёты и поддержка.')]
    assert any(i['code']=='unsupported_absence' for i in inspect_answer(
        'Чем АБ лучше ВГ?', 'Нет прямого сравнения между АБ и ВГ.', rows))
    assert not inspect_answer('Чем Альфа лучше Бета?', 'Нет прямого сравнения между Альфа и Бета.',rows)


def test_unlabelled_next_bullet_does_not_belong_to_previous_column():
    row=dict(ROW,text='Таблица 1, строка 7: Параметр: Контроллер | SSRS: DefaultController | ИСЛО: FormController')
    response='- Для SSRS: DefaultController.\n- Для встроенных отчётов: FormController. [S1]'
    assert not inspect_answer('Сравни контроллеры',response,[row])

from langchain_core.messages import AIMessage

from agentai.answering.engine import answer


USER={'roles':['reader'],'sources':['docs']}


def test_structured_definition_is_explained_by_model_instead_of_quoted_by_template():
    class Store:
        def search(self,*args):return [dict(id='definition',source='docs',citation='description.pdf#L1',text=(
            'АБ «ВГ»\nОбщее описание\nТаблица 1 | Наименование: Информационная система учёта | Код: 3.009 | Идентификатор: UUID'))]
    class Model:
        def __init__(self):self.calls=0
        def bind_tools(self,tools):return self
        def invoke(self,messages):
            self.calls+=1
            return AIMessage(content='АБВГ — информационная система учёта. Она помогает вести учёт данных. [S1]')
    model=Model()
    result=answer('Что такое абвг?',USER,Store(),model)
    assert model.calls==1
    assert result['answer'].startswith('АБВГ — информационная система учёта.')
    assert 'В источнике указано:' not in result['answer']
    assert '3.009' not in result['answer'] and 'UUID' not in result['answer']
    assert result['validation']['passed']


def test_incidental_mention_in_table_does_not_replace_model_answer():
    class Store:
        def search(self,*args):return [dict(id='table',source='docs',citation='fields.pdf#L1',
            text='Типы полей отчёта\nПодпись в АБВГ\nНаименование: Дата начала | Тип: date')]
    class Model:
        def bind_tools(self,tools):return self
        def invoke(self,messages):return AIMessage(content='Значение сокращения зависит от контекста.')
    result=answer('Что такое абвг?',USER,Store(),Model())
    assert result['answer'].startswith('Значение сокращения зависит от контекста.')
    assert 'В источнике указано:' not in result['answer']


def test_known_field_requires_a_source_that_contains_the_field():
    class Store:
        def search(self,*args):return [
            dict(id='field',source='docs',citation='fields.pdf#L1',text='Поле: flag_all_store | Тип данных: int'),
            dict(id='other',source='docs',citation='module.pdf#L1',text='Общее описание модуля отчётов.')]
    class Model:
        def __init__(self,repair=True):self.calls=0;self.repair=repair
        def bind_tools(self,tools):return self
        def invoke(self,messages):
            self.calls+=1
            ref='S1' if self.repair and self.calls>1 else 'S2'
            return AIMessage(content='Поле flag_all_store имеет тип int. ['+ref+']')
    model=Model()
    result=answer('Какое поле открывает все склады?',USER,Store(),model)
    assert model.calls==2 and result['used_sources'][0]['id']=='field'
    assert result['validation']['passed']
    model=Model(repair=False)
    result=answer('Какое поле открывает все склады?',USER,Store(),model)
    assert model.calls==2 and result['retrieval']['extractive_fallback']
    assert result['used_sources'][0]['id']=='field' and result['validation']['passed']


def test_repeated_bad_address_with_a_verified_field_returns_original_source():
    class Store:
        def search(self,*args):return [dict(id='ward',source='docs',citation='document:docs/wards.pdf#L1',
            text='Для одного отделения укажите lpu_section_id_ecp и снимите отметку по ОГРН.')]
    class Model:
        def bind_tools(self,tools):return self
        def invoke(self,messages):return AIMessage(content=(
            'Для отделения укажите lpu_section_id_ecp. [S1] document:docs/invented.pdf#L1'))
    result=answer('Как загрузить палаты одного отделения?',USER,Store(),Model())
    assert result['retrieval']['extractive_fallback'] and result['validation']['passed']
    assert 'invented.pdf' not in result['answer']


def test_bad_address_without_relevant_anchor_does_not_force_unrelated_quotes():
    class Store:
        def search(self,*args):return [dict(id='other',source='docs',citation='document:docs/other.pdf#L1',text='Общее описание.')]
    class Model:
        def bind_tools(self,tools):return self
        def invoke(self,messages):return AIMessage(content='Данных о SLA нет. [S1] document:docs/invented.pdf#L1')
    result=answer('Какой SLA?',USER,Store(),Model())
    assert not result['retrieval']['extractive_fallback']
    assert not result['validation']['passed']

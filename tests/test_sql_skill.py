import json

import pytest
from langchain_core.messages import AIMessage

from agentai.answering.engine import answer, skills
from agentai.answering.skill_selection import select_skill


@pytest.mark.parametrize('question,text,expected',[
    ('Напиши SQL по документу','table description','sql'),
    ('Проверь JOIN','table description','sql'),
    ('Оптимизируй запрос','SELECT x FROM t','sql'),
    ('Покажи схему БД','metadata','sql'),
    ('Объясни код функции','def total(): pass','code'),
    ('Проверь код запроса API','GET /catalog','code'),
    ('Какие значения принимает флаг?','Значения: 1 и 2','documents'),
])
def test_initial_skill_matches_the_work(question,text,expected):
    assert select_skill(question,[{'text':text}]) == expected


def test_sql_skill_is_loaded_before_generation_without_granting_schema_access():
    class Store:
        def search(self,*args):
            return [{'id':'a','source':'docs','citation':'document:docs/query.md#L1-L1',
                     'text':'SELECT wr.ss FROM Written_recipe wr'}]
    class Model:
        def bind_tools(self,tools):
            assert 'database_schema' not in [tool.name for tool in tools]
            return self
        def invoke(self,messages):
            policy=messages[0].content
            loaded=json.loads(policy.split('Already loaded skills:\n')[1])
            assert set(loaded)=={'sql'}
            assert 'не исполняют произвольный SQL' in loaded['sql']
            return AIMessage(content='```sql\nSELECT wr.ss FROM Written_recipe wr\n```\n[S1]')
    result=answer('Напиши SQL',{'roles':['reader'],'sources':['docs']},Store(),Model())
    assert result['validation']['passed']


def test_sql_skill_is_discoverable():
    assert 'sql' in skills()

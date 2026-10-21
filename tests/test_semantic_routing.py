import json
from langchain_core.messages import AIMessage
from qdrant_client import QdrantClient

from agentai.answering.routing import select_documents, spelling_equivalences, expand_candidates
from agentai.answering.session import AnswerSession
from agentai.core import Store, chunks
from agentai.indexing import IndexHeads
from agentai.knowledge.catalog import describe_documents
from test_required_retrieval import Embeddings


def test_catalog_selects_query_relevant_table_line_beyond_heading():
    rows=[dict(source='a',citation='document:a/fields.pdf#L1',text='Поля отчета\n'+'Дата начала\n'*50+
               'Всех = 1; Заявлены, но не выписаны = 2; Выписаны, но не заявлены = 3')]
    catalog=describe_documents(rows,'Какие значения задают вывод всех заявок без выписки?')
    assert 'Всех = 1' in catalog[0]['description']


def test_catalog_obeys_acl_and_active_generations():
    store=Store(Embeddings(),QdrantClient(':memory:'),IndexHeads(in_memory=True))
    store.replace('a',chunks('Старое описание','a','old.md'),document_id='one')
    store.replace('a',chunks('Новое описание','a','new.md'),document_id='one')
    store.replace('secret',chunks('СЕКРЕТ','secret','private.md'))
    catalog=store.document_catalog('описание',['a'])
    assert 'Новое' in str(catalog) and 'Старое' not in str(catalog) and 'СЕКРЕТ' not in str(catalog)
    assert store.document_catalog('описание',[])==[]
    assert store.search('описание',['a'],documents=[('secret','private.md')])==[]


def test_selector_batches_and_validates_model_ids():
    catalog=[dict(n=i,key=('a',str(i)),description='Материал '+str(i)) for i in range(1,18)]
    class Model:
        calls=0
        def invoke(self,messages):
            self.calls+=1
            batch=json.loads(messages[-1].content)['catalog']
            assert len(batch)<=8
            return AIMessage(content=json.dumps({'documents':[batch[-1]['n'],99999]}))
    model=Model()
    keys,_=select_documents('Текущий вопрос',catalog,model)
    assert model.calls==3 and keys==[('a','8'),('a','16'),('a','17')]


def test_spelling_equivalence_is_derived_from_sources_not_a_domain_dictionary():
    assert spelling_equivalences('Что такое абвг?', [{'description':'АБ «ВГ»\nОбщее описание'}])=={'абвг':'АБ ВГ'}
    assert spelling_equivalences('Что такое неизвестно?', [{'description':'ИС «ЛО»'}])=={}


def test_bad_optional_selector_keeps_initial_evidence():
    class CatalogStore:
        def search(self,*args):return [dict(id='one',source='a',text='known',citation='one.md')]
        def document_catalog(self,*args):return [dict(n=1,key=('a','one.md'),description='Описание')]
    class Model:
        def invoke(self,*args):raise RuntimeError('unavailable')
    session=AnswerSession('Новый вопрос',{'roles':['reader'],'sources':['a']},CatalogStore())
    session.retrieve(); expand_candidates(session,Model())
    assert session.rows[0]['text']=='known'


def test_inferred_followup_keeps_current_global_results():
    class Search:
        def __init__(self):self.queries=[]
        def search(self,query,sources):
            self.queries.append(query)
            identity='context' if 'old.md' in query else 'fresh'
            return [dict(id=identity,source='a',citation=identity+'.md',text=identity)]
    store=Search(); question='Подробнее: как при этом устроен доступ?'
    history=[dict(question='old.md',answer='answer',context={'selected':[['a','document:a/old.md']]})]
    session=AnswerSession(question,{'roles':['reader'],'sources':['a']},store,history)
    session.retrieve()
    assert store.queries[0]==question
    assert {row['id'] for row in session.rows}=={'context','fresh'}
    assert not session.scope_ready

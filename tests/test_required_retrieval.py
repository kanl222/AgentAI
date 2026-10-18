from langchain_core.messages import AIMessage, ToolMessage
from qdrant_client import QdrantClient
import pytest
from fastapi import HTTPException
from agentai.agent import answer
from agentai.core import Store, chunks


class Embeddings:
    def embed_documents(self,texts):return [[1.,0.,0.] for _ in texts]
    def embed_query(self,text):return [1.,0.,0.]


class SkipsSearch:
    def bind_tools(self,tools):return self
    def invoke(self,messages):
        found=any(isinstance(m,ToolMessage) and 'ПотреблениеТоваровПоНазначениямВрачей' in str(m.content) for m in messages)
        return AIMessage(content='Найдены типы документов' if found else 'Нет доказательств о типах документов')


class LoadsSkillThenAnswers(SkipsSearch):
    def __init__(self):self.calls=0
    def invoke(self,messages):
        self.calls+=1
        if self.calls==1:
            return AIMessage(content='',tool_calls=[{'id':'skill','name':'load_skill','args':{'name':'documents'}}])
        return super().invoke(messages)


@pytest.mark.parametrize('model',[SkipsSearch,LoadsSkillThenAnswers])
def test_known_document_is_available_even_when_model_skips_search(model):
    store=Store(Embeddings(),QdrantClient(':memory:'))
    store.replace('docs-demo',chunks('Типы документов в ИС ЛО\nПотреблениеТоваровПоНазначениямВрачей','docs-demo','types.pdf?page=1'))
    store.replace('private',chunks('PRIVATE SECRET','private','secret.pdf'))
    result=answer('Расскажи про типы документов в ИС ЛО',{'roles':['reader'],'sources':['docs-demo']},store,model())
    assert 'Найдены типы' in result['answer']
    assert result['sources'] and all(r['source']=='docs-demo' for r in result['sources'])
    assert 'PRIVATE SECRET' not in str(result)


def test_search_failure_is_not_presented_as_absent_document():
    class OfflineStore:
        def search(self,*args):raise RuntimeError('embedding server offline')
    with pytest.raises(HTTPException) as error:
        answer('Типы документов',{'roles':['reader'],'sources':['docs-demo']},OfflineStore(),SkipsSearch())
    assert error.value.status_code==503


def test_exact_document_title_beats_dense_noise():
    store=Store(Embeddings(),QdrantClient(':memory:'))
    for i in range(7):
        store.replace(str(i),chunks('ЛО ИС в документов Типы',str(i),'noise'+str(i)))
    store.replace('target',chunks('Типы документов в ИС ЛО','target','types.pdf'))
    result=store.search('Типы документов в ИС "ЛО"',[str(i) for i in range(7)]+['target'])
    assert result[0]['source']=='target'


def test_later_empty_search_keeps_found_evidence():
    class StoreWithEmptyRefinement:
        def search(self,query,sources):
            if query=='unrelated':return []
            return [{'id':'known','source':'docs-demo','text':'ПотреблениеТоваровПоНазначениямВрачей','citation':'types.pdf?page=1'}]
    class RefinesThenAnswers(SkipsSearch):
        def __init__(self):self.calls=0
        def invoke(self,messages):
            self.calls+=1
            if self.calls==1:
                return AIMessage(content='',tool_calls=[{'id':'refinement','name':'search','args':{'query':'unrelated'}}])
            return super().invoke(messages)
    result=answer('Типы документов',{'roles':['reader'],'sources':['docs-demo']},StoreWithEmptyRefinement(),RefinesThenAnswers())
    assert 'Найдены типы' in result['answer']
    assert result['sources'][0]['citation']=='types.pdf?page=1'


def test_empty_authorized_index_remains_empty():
    store=Store(Embeddings(),QdrantClient(':memory:'))
    result=answer('Типы документов',{'roles':['reader'],'sources':['docs-demo']},store,SkipsSearch())
    assert result['sources']==[]


def test_exact_heading_returns_original_without_model_invention():
    class MustNotGenerate:
        def bind_tools(self,tools):return self
        def invoke(self,messages):raise AssertionError('Exact heading lookup must not paraphrase evidence')
    store=Store(Embeddings(),QdrantClient(':memory:'))
    content='Типы документов в ИС "ЛО"\nПотреблениеТоваровПоНазначениямВрачей\nВнутреннееПотреблениеТоваров'
    store.replace('docs-demo',chunks(content,'docs-demo','document:docs-demo/types.pdf?page=1'))
    store.replace('docs-demo-other',chunks('Другой текст','docs-demo-other','document:docs-demo-other/other.pdf?page=1'))
    result=answer('Типы документов в ИС «ЛО»',{'roles':['reader'],'sources':['docs-demo','docs-demo-other']},store,MustNotGenerate())
    assert 'ВнутреннееПотреблениеТоваров' in result['answer']
    assert 'Другой текст' not in str(result)
    assert len(result['sources'])==1 and result['steps']==0

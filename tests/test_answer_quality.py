from langchain_core.messages import AIMessage
from qdrant_client import QdrantClient

from agentai.agent import answer
from agentai.core import Store, chunks
from test_required_retrieval import Embeddings


def test_explicit_filename_excludes_other_documents_and_keeps_all_chunks():
    store = Store(Embeddings(), QdrantClient(':memory:'))
    store.replace('demo', chunks('orders code\n' * 60, 'demo', 'document:demo/source-bundle.md@v1'))
    store.replace('other', chunks('orders code', 'other', 'document:other/policy.pdf@v2'))
    rows = store.search('Оптимизируй код из source-bundle.md', ['demo', 'other'])
    assert len(rows) == 2
    assert all('source-bundle.md' in row['citation'] for row in rows)
    assert store.search('Прочитай missing.md', ['demo', 'other']) == []


def test_project_identifier_excludes_dense_noise():
    store = Store(Embeddings(), QdrantClient(':memory:'))
    store.replace('demo', chunks('OPTIMIZATION_DEMO_ORDERS\norders code', 'demo', 'bundle.md'))
    store.replace('other', chunks('другой документ', 'other', 'policy.pdf'))
    rows = store.search('Оптимизируй OPTIMIZATION_DEMO_ORDERS', ['demo', 'other'])
    assert [row['source'] for row in rows] == ['demo']


def test_truncated_generation_is_reported():
    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content='```python\ndef incomplete(', response_metadata={'finish_reason': 'length'})

    class EmptyStore:
        def search(self, *args):
            return []

    result = answer('Напиши код', {'roles': ['reader'], 'sources': []}, EmptyStore(), Model())
    assert result['finish_reason'] == 'length'
    assert 'оборван' in result['answer'].lower()


def test_refinement_cannot_add_unrelated_sources_to_explicit_file():
    class Model:
        calls = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content='', tool_calls=[{
                    'id': 'refine', 'name': 'search', 'args': {'query': 'other'},
                }])
            return AIMessage(content='Предложение по коду')

    class SearchStore:
        def search(self, query, sources):
            if query == 'other':
                return [{'id': 'other', 'source': 'demo', 'citation': 'document:demo/policy.pdf@v2#L1', 'text': 'other'}]
            return [{'id': 'code', 'source': 'demo', 'citation': 'document:demo/source-bundle.md@v1#L1', 'text': 'code'}]

    result = answer('Проверь source-bundle.md', {'roles': ['reader'], 'sources': ['demo']}, SearchStore(), Model())
    assert [r['id'] for r in result['sources']] == ['code']

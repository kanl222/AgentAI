import re
import importlib.util
from pathlib import Path

from agentai.ingestion.extract import chunks
from agentai.settings import ChunkSettings, RetrievalSettings
from agentai.answering.session import AnswerSession
from agentai.knowledge.store import Store
from agentai.knowledge.heads import IndexHeads
from qdrant_client import QdrantClient


class Embeddings:
    def embed_documents(self, texts): return [[1., 0., 0.] for _ in texts]
    def embed_query(self, text): return [1., 0., 0.]


def test_overlap_preserves_a_fact_across_a_chunk_boundary(monkeypatch):
    # A name and its value straddle the 50-line boundary of the old splitter.
    lines = ['filler'] * 49 + ['Recovery window:', 'within 48 hours'] + ['filler'] * 20
    monkeypatch.setenv('RAG_CHUNK_OVERLAP', '0')
    baseline = list(chunks('\n'.join(lines), 'docs', 'policy.md'))
    assert not any('Recovery window:\nwithin 48 hours' in row['text'] for row in baseline)
    monkeypatch.setenv('RAG_CHUNK_OVERLAP', '400')
    improved = list(chunks('\n'.join(lines), 'docs', 'policy.md'))
    assert any('Recovery window:\nwithin 48 hours' in row['text'] for row in improved)
    for row in improved:
        first, last = map(int, re.search(r'#L(\d+)-L(\d+)', row['citation']).groups())
        assert row['text'] == '\n'.join(lines[first-1:last])


def test_long_lines_are_bounded_complete_and_deterministic(monkeypatch):
    monkeypatch.setenv('RAG_CHUNK_CHARS', '500')
    text = ''.join(chr(0x400+i % 200) for i in range(10001))
    rows = list(chunks(text, 'docs', 'long.md'))
    assert rows == list(chunks(text, 'docs', 'long.md'))
    assert all(len(row['text']) <= 500 for row in rows)
    assert all(row['citation'] == 'long.md#L1-L1' for row in rows)
    # Every position is retained in order, allowing the configured overlap.
    reconstructed = rows[0]['text']
    for row in rows[1:]:
        suffix = row['text'].split('\n', 1)[-1]
        reconstructed += suffix
    assert reconstructed == text


def test_rag_configuration_is_bounded(monkeypatch):
    monkeypatch.setenv('RAG_CHUNK_CHARS', '500')
    monkeypatch.setenv('RAG_CHUNK_OVERLAP', '99999')
    monkeypatch.setenv('RAG_TOP_K', '99999')
    monkeypatch.setenv('RAG_CONTEXT_CHARS', '-1')
    assert ChunkSettings.from_env().overlap == 125
    assert RetrievalSettings.from_env().top_k == 12
    session = AnswerSession('q', {}, None)
    session.rows = [{'id': '1', 'text': 'a' * 9000}]
    assert len(session.packed()[0]['text']) < 2000


def test_search_limits_keep_source_permissions(monkeypatch):
    monkeypatch.setenv('RAG_TOP_K', '2')
    store = Store(Embeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
    store.replace('public', chunks('public\n'*200, 'public', 'manual.md'))
    store.replace('private', chunks('private', 'private', 'secret.md'))
    rows = store.search('public', ['public'])
    assert len(rows) == 2
    assert {row['source'] for row in rows} == {'public'}
    assert store.search('secret.md', ['public']) == []


def test_evaluator_requires_the_fact_and_checks_empty_scopes(monkeypatch):
    path = Path(__file__).resolve().parents[1]/'scripts/evaluate_rag.py'
    spec = importlib.util.spec_from_file_location('rag_evaluation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = '\n'.join(['filler']*49+['Recovery:', '48 hours'])
    dataset = {'documents': [{'id':'d', 'filename':'manual.md', 'text':text}],
               'cases': [{'query':'manual.md', 'expected':'d', 'expected_text':['Recovery:\n48 hours']},
                         {'query':'manual.md', 'expected':None, 'sources':[]}]}
    monkeypatch.setenv('RAG_CHUNK_OVERLAP', '0')
    assert module.evaluate(dataset, Embeddings())['pass_rate'] == 0.5
    monkeypatch.setenv('RAG_CHUNK_OVERLAP', '400')
    assert module.evaluate(dataset, Embeddings())['pass_rate'] == 1

"""Lexical retrieval must tolerate typography without domain-specific aliases."""
import pytest
from qdrant_client import QdrantClient

from agentai.indexing import IndexHeads
from agentai.core import Store, chunks
from agentai.knowledge.text import lexical_tokens
from test_required_retrieval import Embeddings


@pytest.mark.parametrize('text,query', [('ИС «ЛО»', 'исло'), ('АБ "ВГ"', 'абвг'),
                                      ('Отчеты', 'отчёты')])
def test_typographic_variants_share_lexical_features(text, query):
    assert set(lexical_tokens(text)) & set(lexical_tokens(query))


@pytest.mark.parametrize('heading,question', [('ИС «ЛО»', 'что такое исло'),
                                            ('АБ "ВГ"', 'что такое абвг')])
def test_overview_survives_dense_noise_for_compact_abbreviation(heading, question):
    class NoisyEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[0.,1.,0.] if 'Общее описание' in text else [1.,0.,0.] for text in texts]
    store = Store(NoisyEmbeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
    for i in range(25):
        store.replace(str(i), chunks('Что такое программа? Инструкция по учеткам.', str(i), str(i)+'.pdf'))
    store.replace('overview', chunks(heading+'\nОбщее описание информационной системы.', 'overview', 'overview.pdf'))
    rows = store.search(question, [str(i) for i in range(25)]+['overview'])
    assert any(row['source']=='overview' for row in rows[:3])


def test_abbreviation_features_do_not_join_unrelated_lines():
    assert 'исло' not in lexical_tokens('ИС\nЛО')


def test_document_heading_inside_natural_question_beats_dense_noise():
    class NoisyEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[0.,1.,0.] if text.startswith('Типы документов') else [1.,0.,0.] for text in texts]
    store = Store(NoisyEmbeddings(), QdrantClient(':memory:'), IndexHeads(in_memory=True))
    for i in range(25):
        store.replace(str(i), chunks('Шум\nЛО ИС в документов Типы', str(i), str(i)+'.pdf'))
    store.replace('types', chunks('Типы документов в ИС «ЛО»\n'+('Описание операций. '*70), 'types', 'types.pdf'))
    rows = store.search('Какие бывают типы документов в ИС ЛО?', [str(i) for i in range(25)]+['types'])
    assert rows[0]['source']=='types'

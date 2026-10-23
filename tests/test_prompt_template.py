from agentai.answering.prompt_template import format_rag_prompt
from agentai.agent import answer, answer_with_template
from langchain_core.messages import AIMessage


def test_format_rag_prompt_no_citations_default():
    passages = [
        {
            "file": "docs/contract.pdf",
            "shard": "0",
            "score": 0.8923,
            "text": "Поставщик обязуется поставить товар в течение 10 дней.",
        },
        {
            "fileName": "src/service.py",
            "shard": "1",
            "score": 0.741,
            "text": "def calculate_delivery(): return 10",
        },
    ]
    query = "Какой срок поставки?"

    prompt = format_rag_prompt(passages, query)

    assert "Пассаж #1 (file: docs/contract.pdf, shard: 0, score: 0.892):" in prompt
    assert '"Поставщик обязуется поставить товар в течение 10 дней."' in prompt
    assert "Пассаж #2 (file: src/service.py, shard: 1, score: 0.741):" in prompt
    assert "Не указывай цитаты, сноски, номера источников" in prompt
    assert "User Query:\n\nКакой срок поставки?" in prompt


def test_format_rag_prompt_custom_instructions():
    passages = [{"file": "test.txt", "shard": "0", "score": 0.9, "text": "Текст"}]
    prompt = format_rag_prompt(passages, "Вопрос?", instructions="Кастомные правила.")
    assert "Кастомные правила." in prompt
    assert "User Query:\n\nВопрос?" in prompt


def test_format_rag_prompt_empty():
    prompt = format_rag_prompt([], "Вопрос без документов?")
    assert "Важно: В проиндексированных документах не найдено релевантных материалов" in prompt
    assert "без ссылок на источники" in prompt
    assert "User Query:\n\nВопрос без документов?" in prompt


def test_agent_answer_with_template():
    class DummyStore:
        def search(self, query, sources):
            return [{
                'id': 'doc-1',
                'text': 'Срок доставки 5 дней.',
                'source': 'docs',
                'citation': 'document:docs/delivery.pdf@v1?page=1#L1-L2',
            }]

    class DummyModel:
        def __init__(self):
            self.captured_messages = []

        def invoke(self, messages):
            self.captured_messages = messages
            return AIMessage(content='Срок доставки составляет 5 дней.')

    user = {'roles': ['reader'], 'sources': ['docs']}
    model = DummyModel()
    result = answer_with_template('Сроки доставки?', user, DummyStore(), model)

    assert result['answer'] == 'Срок доставки составляет 5 дней.'
    assert result['validation']['passed'] is True
    assert result['retrieval']['template_mode'] is True
    assert 'docs/delivery.pdf' in result['prompt']
    assert 'Не указывай цитаты, сноски, номера источников' in result['prompt']
    assert 'User Query:\n\nСроки доставки?' in result['prompt']

    # Verify answer(..., use_template=True) routing
    result2 = answer('Сроки доставки?', user, DummyStore(), model, use_template=True)
    assert result2['answer'] == 'Срок доставки составляет 5 дней.'

import json

from langchain_core.messages import AIMessage, ToolMessage

from agentai.agent import answer


USER = {'roles': ['reader'], 'sources': ['docs']}


def row(identity, text):
    return {'id': identity, 'source': 'docs', 'text': text,
            'citation': f'document:docs/{identity}.pdf?page=1#L1-L3'}


class Model:
    def bind_tools(self, tools):
        return self


def test_successful_refinement_keeps_previous_evidence():
    class Store:
        def search(self, query, sources):
            return [row('first', 'KNOWN_TABLE')] if query == 'Вопрос' else [row('second', 'DETAIL')]

    class Refines(Model):
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content='', tool_calls=[
                    {'id': 'refine', 'name': 'search', 'args': {'query': 'Уточнение'}}])
            evidence = next(m.content for m in messages if isinstance(m, ToolMessage))
            assert 'KNOWN_TABLE' in evidence and 'DETAIL' in evidence
            return AIMessage(content='Оба фрагмента доступны')

    answer('Вопрос', USER, Store(), Refines())


def test_read_source_keeps_evidence_from_other_document():
    class Store:
        def search(self, query, sources):
            return [row('first', 'ANCHOR'), row('second', 'OTHER_DOCUMENT')]

        def read_source(self, identity, sources, offset, limit):
            return [row('neighbor', 'NEIGHBOR')]

    class Reads(Model):
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content='', tool_calls=[
                    {'id': 'read', 'name': 'read_source', 'args': {'chunk_id': 'first'}}])
            evidence = next(m.content for m in messages if isinstance(m, ToolMessage))
            assert 'OTHER_DOCUMENT' in evidence and 'NEIGHBOR' in evidence
            return AIMessage(content='Сопоставлены два документа')

    answer('Сравни документы', USER, Store(), Reads())


def test_context_budget_does_not_silently_drop_later_hits():
    class Store:
        def search(self, query, sources):
            return [row(str(i), f'RELEVANT_{i} ' + 'текст ' * 560) for i in range(6)]

    class Inspects(Model):
        def invoke(self, messages):
            content = next(m.content for m in messages if isinstance(m, ToolMessage))
            entries = json.loads(content)
            assert len(content) <= 12000
            assert len(entries) == 6
            assert all(f'RELEVANT_{i}' in entries[i]['text'] for i in range(6))
            assert all(entry.get('text_truncated') for entry in entries)
            return AIMessage(content='Все шесть фрагментов доступны')

    answer('Найди сведения', USER, Store(), Inspects())


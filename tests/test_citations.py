from langchain_core.messages import AIMessage, ToolMessage

from agentai.agent import answer


CITATION = 'document:docs/report.pdf@v1?page=1#L1-L3'
ROW = {'id': 'report-row', 'source': 'docs', 'citation': CITATION,
       'text': 'Controller: DefaultController'}
USER = {'roles': ['reader'], 'sources': ['docs']}


class Store:
    def search(self, *args):
        return [dict(ROW)]


class Model:
    def __init__(self, text):
        self.text = text

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        evidence = next(m.content for m in messages if isinstance(m, ToolMessage))
        assert '"ref": "S1"' in evidence
        return AIMessage(content=self.text)


def test_backend_resolves_known_reference_and_rejects_unknown_reference():
    result = answer('Назови контроллер', USER, Store(),
                    Model('DefaultController [S1]. Другой факт [S99].'))
    assert CITATION in result['answer']
    assert len(result['used_sources']) == 1
    assert '[S99]' not in result['answer']
    assert any('неподтвержд' in warning.lower() for warning in result['warnings'])


def test_generated_source_url_is_replaced_but_code_example_url_is_preserved():
    text = ('DefaultController [S1](https://example.com/fabricated).\n\n'
            '```python\nurl = "https://example.com/api"\n```')
    result = answer('Назови контроллер', USER, Store(), Model(text))
    assert 'https://example.com/fabricated' not in result['answer']
    assert 'https://example.com/api' in result['answer']
    assert CITATION in result['answer']


def test_legacy_exact_source_label_does_not_keep_a_fabricated_destination():
    text = f'Контроллер [{CITATION}](https://example.com/fabricated).'
    result = answer('Назови контроллер', USER, Store(), Model(text))
    assert 'example.com' not in result['answer']
    assert result['used_sources'][0]['citation'] == CITATION


def test_generated_document_location_cannot_claim_an_unretrieved_source():
    result = answer('Назови контроллер', USER, Store(),
                    Model('Ответ document:other/private.pdf?page=8#L1-L5.'))
    assert 'document:other/' not in result['answer']
    assert result['used_sources'] == []
    assert any('неподтвержд' in warning.lower() for warning in result['warnings'])


def test_missing_citation_gets_one_bounded_correction_with_same_evidence():
    class OmitsThenCites(Model):
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            assert any(isinstance(m, ToolMessage) and '"ref": "S1"' in m.content for m in messages)
            return AIMessage(content='DefaultController' + (' [S1]' if self.calls > 1 else ''))

    model = OmitsThenCites('')
    result = answer('Назови контроллер', USER, Store(), model)
    assert model.calls == 2
    assert result['steps'] == 2
    assert result['used_sources'][0]['citation'] == CITATION


def test_correction_respects_step_budget():
    result = answer('Назови контроллер', USER, Store(), Model('DefaultController'), max_steps=1)
    assert result['steps'] == 1
    assert result['used_sources'] == []


def test_russian_source_marker_resolves_only_to_retrieved_reference():
    result = answer('Назови контроллер', USER, Store(), Model('DefaultController [С1]'))
    assert result['used_sources'][0]['citation'] == CITATION


def test_labelled_marker_resolves_without_changing_its_source():
    result = answer('Назови контроллер', USER, Store(), Model('DefaultController [Ссылка на источник: S1]'))
    assert result['used_sources'][0]['citation'] == CITATION


def test_failed_optional_correction_keeps_original_answer_and_reports_it():
    class FailsCorrection(Model):
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError('model unavailable')
            return AIMessage(content='DefaultController')

    result = answer('Назови контроллер', USER, Store(), FailsCorrection(''))
    assert result['answer'].startswith('DefaultController')
    assert any('первоначальный' in warning for warning in result['warnings'])

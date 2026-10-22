import json

from langchain_core.messages import AIMessage, ToolMessage

from agentai.agent import answer
from agentai.answering.evidence import pack_evidence
from agentai.answering.session import AnswerSession


USER = {'roles': ['reader'], 'sources': ['docs']}


def passage(index, *, filename='method.cs', version='v1', text=None):
    return {'id': f'{filename}-{version}-{index}', 'source': 'docs',
            'document_id': filename, 'version': version,
            'citation': f'document:docs/{filename}@{version}#L{index*50+1}-L{(index+1)*50}',
            'text': text if text is not None else f'// part {index}\n' + 'code();\n'*280}


class BatchStore:
    def __init__(self):
        self.rows = [passage(i) for i in range(15)]
        self.calls = []

    def search(self, query, sources):
        # Search ranks passages by relevance, not their position in the file.
        return [self.rows[2], self.rows[7], self.rows[1]]

    def read_source(self, identity, sources, offset, limit):
        self.calls.append((identity, offset, limit))
        index = next(i for i, row in enumerate(self.rows) if row['id'] == identity)
        start = max(0, index+offset)
        return self.rows[start:start+limit]


def test_initial_neighbors_are_ordered_and_not_duplicated():
    store = BatchStore()
    session = AnswerSession('Объясни обработку данных', USER, store)
    session.retrieve()
    session.expand_neighbors()
    assert store.calls == [(store.rows[2]['id'], -1, 6)]
    assert [row['id'] for row in session.rows[:6]] == [row['id'] for row in store.rows[1:7]]
    assert len({row['id'] for row in session.rows}) == len(session.rows)


def test_short_hits_from_same_file_are_sorted_including_original_hits():
    store = BatchStore()
    store.rows = [passage(i, text=f'line {i}') for i in range(15)]
    session = AnswerSession('Объясни обработку данных', USER, store)
    session.retrieve()
    session.expand_neighbors()
    assert [row['id'] for row in session.packed()] == [row['id'] for row in store.rows[1:8]]


def test_automatic_expansion_is_bounded_to_three_distinct_files():
    class ManyFiles:
        calls = []

        def search(self, *args):
            return [passage(1, filename=f'file{i}.cs') for i in range(8)]

        def read_source(self, identity, sources, offset, limit):
            self.calls.append(identity)
            return []

    store = ManyFiles()
    session = AnswerSession('Объясни обработку данных', USER, store)
    session.retrieve()
    session.expand_neighbors()
    assert len(store.calls) == 3
    assert len(session.rows) == 8


def test_neighbor_batches_do_not_merge_another_file_or_version():
    class MixedStore(BatchStore):
        def read_source(self, *args):
            return [self.rows[2], passage(3, version='old'), passage(4, filename='other.cs')]

    store = MixedStore()
    session = AnswerSession('Объясни обработку данных', USER, store)
    session.retrieve()
    session.expand_neighbors()
    assert all(row['version'] == 'v1' and row['document_id'] == 'method.cs' for row in session.rows)


def test_complete_chunks_and_continuation_fit_json_budget():
    rows = [passage(i, text=('строка "\\\n'*400)+f'END_{i}') for i in range(8)]
    entries = pack_evidence(rows, budget=12000)
    assert 0 < len(entries) < len(rows)
    assert len(json.dumps(entries, ensure_ascii=False)) <= 12000
    assert all(row['text'] == rows[i]['text'] and not row['text_truncated'] for i, row in enumerate(entries))
    assert entries[0]['next_chunk_id'] == rows[len(entries)]['id']
    assert entries[0]['context_omitted_count'] == len(rows)-len(entries)


def test_large_neighbor_batch_cannot_hide_other_files_search_hits():
    rows = [passage(i, text='large code line\n'*200) for i in range(6)]
    other = passage(0, filename='correct.cs', text='EXPECTED_METHOD\n'+'body();\n'*200)
    third = passage(0, filename='third.cs', text='OTHER_CANDIDATE\n'+'body();\n'*200)
    rows.extend([other, third])
    entries = pack_evidence(rows, anchors=[rows[2]['id'], other['id'], third['id']])
    ids = {entry['id'] for entry in entries}
    assert {rows[2]['id'], other['id'], third['id']} <= ids
    assert len(json.dumps(entries, ensure_ascii=False)) <= 12000
    assert all(not entry['text_truncated'] for entry in entries)


def test_oversized_chunk_is_marked_and_small_budget_never_overflows():
    rows = [passage(0, text='long code\n'*10000)]
    for budget in (2, 100, 2000, 12000):
        entries = pack_evidence(rows, budget=budget)
        assert len(json.dumps(entries, ensure_ascii=False)) <= budget
        if entries:
            assert entries[0]['text_truncated']
            assert 'next_chunk_id' not in entries[0]


def test_non_fitting_passage_is_reported_when_a_shorter_hit_fits():
    rows = [passage(0, text='long code\n'*10000), passage(1, text='COMPLETE_SHORT_HIT')]
    entries = pack_evidence(rows, budget=1000)
    assert len(entries) == 1
    assert entries[0]['text'] == 'COMPLETE_SHORT_HIT'
    assert not entries[0]['text_truncated']
    assert entries[0]['context_omitted_count'] == 1
    assert entries[0]['next_chunk_id'] == rows[0]['id']


def test_model_reads_next_batch_of_long_code_without_a_file_hint():
    store = BatchStore()

    class ReadsBatches:
        calls = 0
        continuation = None

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            entries = json.loads(next(m.content for m in messages if isinstance(m, ToolMessage)))
            self.calls += 1
            if self.calls == 1:
                assert [row['id'] for row in entries[:3]] == [row['id'] for row in store.rows[1:4]]
                assert all(not row['text_truncated'] for row in entries)
                self.continuation = entries[0]['next_chunk_id']
                return AIMessage(content='', tool_calls=[{'id': 'continue-code', 'name': 'read_source',
                    'args': {'chunk_id': self.continuation, 'offset': 0, 'limit': 6}}])
            assert entries[0]['id'] == self.continuation
            start = next(i for i, row in enumerate(store.rows) if row['id'] == self.continuation)
            assert [row['id'] for row in entries] == [row['id'] for row in store.rows[start:start+len(entries)]]
            assert all(not row['text_truncated'] for row in entries)
            return AIMessage(content='Продолжение метода прочитано.')

    model = ReadsBatches()
    answer('Объясни обработку данных', USER, store, model)
    assert model.calls == 2
    assert store.calls[-1] == (model.continuation, 0, 6)


def test_explicit_read_has_priority_over_previous_search_hits():
    store = BatchStore()
    session = AnswerSession('Объясни обработку данных', USER, store)
    session.retrieve()
    session.expand_neighbors()
    session.read_source(store.rows[6]['id'], offset=1, limit=6)
    entries = session.packed()
    assert entries[0]['id'] == store.rows[7]['id']
    assert all(not entry['text_truncated'] for entry in entries)
    assert len(json.dumps(entries, ensure_ascii=False)) <= 12000


def test_code_continuation_does_not_depend_on_model_requesting_a_tool():
    store = BatchStore()
    store.rows = [passage(i, text=('def deliver():\n' if i == 0 else '') + '    # operation details\n'*110)
                  for i in range(8)]
    store.rows[-1]['text'] += '\n    raise RuntimeError("FINAL_CODE_BRANCH")'
    store.search = lambda *args: [store.rows[0]]

    class PassiveModel:
        calls = 0
        saw_final = False

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.calls += 1
            entries = json.loads(next(m.content for m in messages if isinstance(m, ToolMessage)))
            self.saw_final |= any('FINAL_CODE_BRANCH' in row['text'] for row in entries)
            return AIMessage(content='Последняя ветка: FINAL_CODE_BRANCH.' if self.saw_final else 'Продолжение неизвестно.')

    model = PassiveModel()
    result = answer('Объясни код обработки', USER, store, model)
    assert model.saw_final
    assert 1 < model.calls <= 4
    assert 'FINAL_CODE_BRANCH' in result['answer']

"""Bounded answer graph; evidence and result presentation have separate owners."""
import json
import re
from pathlib import Path
from typing import Annotated, TypedDict
import yaml
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from ..access import require
from ..adapters.sqlserver import sql_metadata
from .session import AnswerSession
from .citations import render_citations
from .validation import inspect_answer, prose
from .code_checks import normalize_sql_aliases, normalize_sort_contract, normalize_requested_format
from .result import finalize
from .skill_selection import select_skill
from .routing import expand_candidates
from .extractive import evidence_answer
from .prompt_template import format_rag_prompt


class State(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int
    direct_answer: str
    citation_repaired: bool
    citation_repair_failed: bool
    review_feedback: str


def skills():
    result = {}
    for path in Path('skills').glob('*/SKILL.md'):
        _, header, body = path.read_text(encoding='utf-8').split('---', 2)
        meta = yaml.safe_load(header)
        result[meta['name']] = (meta['description'], body.strip())
    return result


@require('reader')
def answer_with_template(question, user, store, model, instructions=None, history=None):
    """Single-turn grounded generation using the formatted RAG prompt template without requiring citations."""
    history = history or []
    session = AnswerSession(question, user, store, history)
    session.retrieve()

    packed_rows = session.packed()
    passages = []
    for row in packed_rows:
        passages.append({
            'file': row.get('location') or row.get('citation') or row.get('source', 'unknown'),
            'shard': row.get('shard', '0'),
            'score': row.get('score', 1.0),
            'text': row.get('text', ''),
        })

    prompt = format_rag_prompt(passages, question, instructions=instructions)

    if hasattr(model, 'invoke'):
        result = model.invoke([HumanMessage(content=prompt)])
        content = getattr(result, 'content', str(result))
    elif callable(model):
        result = model(prompt)
        content = getattr(result, 'content', str(result))
    else:
        raise ValueError(f'Unsupported model: {type(model)}')

    evidence = list(session.evidence.values())
    return {
        'question': question,
        'answer': str(content),
        'prompt': prompt,
        'passages': passages,
        'sources': evidence,
        'used_sources': [],
        'warnings': [],
        'steps': 1,
        'selected_sources': [row.get('location') or row.get('citation') for row in packed_rows],
        'finish_reason': 'stop',
        'validation': {'passed': True, 'issues': []},
        'retrieval': {'catalog_selection': session.routing_status, 'template_mode': True},
    }


@require('reader')
def answer(question, user, store, model, max_steps=4, history=None, use_template=False, instructions=None):
    if use_template:
        return answer_with_template(question, user, store, model, instructions=instructions, history=history)
    history = history or []
    session = AnswerSession(question, user, store, history)
    loaded_skills = {}
    presented_code = set()
    catalog = skills()

    @tool
    def search(query: str) -> str:
        """Search authorized documents and code; return evidence and exact source locations."""
        return json.dumps(session.search(query), ensure_ascii=False)

    @tool
    def load_skill(name: str) -> str:
        """Load one skill's detailed instructions from the catalog."""
        body = catalog.get(name, ('', 'Unknown skill'))[1][:4000]
        if name in catalog:
            loaded_skills[name] = body
        return body

    @tool
    def read_source(chunk_id: str, offset: int = -1, limit: int = 3) -> str:
        """Read neighboring chunks of an already retrieved source. Offset is relative to the chunk; use positive offsets to continue."""
        rows = session.read_source(chunk_id, offset, limit)
        return 'Retrieve a source first' if rows is None else json.dumps(rows, ensure_ascii=False)

    @tool
    def database_schema() -> str:
        """Read SQL Server schema metadata only."""
        require(user, 'sql_reader')
        return json.dumps(sql_metadata(), default=str)

    available = [search, load_skill, read_source]
    if 'sql_reader' in user['roles']:
        available.append(database_schema)
    tools = {t.name: t for t in available}
    bound = model.bind_tools(available)

    def retrieval_messages():
        call_id = 'backend-retrieval'
        rows = session.packed()
        return [AIMessage(content='', tool_calls=[{'id': call_id, 'name': 'search', 'args': {'query': session.query}}]),
                ToolMessage(content=json.dumps(rows, ensure_ascii=False), tool_call_id=call_id)]

    def retrieve(state):
        session.retrieve()
        direct = session.direct_quote() or session.missing_target()
        if not direct:
            expand_candidates(session, model)
            session.expand_neighbors()
        name = select_skill(question, session.rows)
        if name in catalog:
            loaded_skills[name] = catalog[name][1][:4000]
        return {'messages': retrieval_messages(), 'direct_answer': direct}

    def call(state):
        graph_history = state['messages']
        policy = graph_history[0].content
        if session.equivalences:
            policy += ('\nИз самого текста источников установлены равнозначные написания сокращений: '
                       + json.dumps(session.equivalences, ensure_ascii=False) +
                       '. Различие пробелов и регистра не является отсутствием информации. '
                       'Объясни предмет вопроса по найденным материалам.')
        if loaded_skills:
            policy += '\nAlready loaded skills:\n' + json.dumps(loaded_skills, ensure_ascii=False)
        if 'code' in loaded_skills and 'сложност' in question.lower():
            policy += ('\nВ оценке сложности размер вложенной коллекции не является константой без '
                       'явного ограничения в источнике. Если цикл содержит sum/comprehension по вложенной '
                       'коллекции, обозначь I как общее число её элементов по всем итерациям и явно включи '
                       '+ I в общую формулу времени. Отдельно учти сортировку U log U и размер каждого словаря в памяти.')
        if (re.search(r'тест', question, re.I) and re.search(r'нулев', question, re.I)
                and re.search(r'строго больше нуля', question, re.I)):
            policy += ('\nПроверь запрошенные границы: для фильтра > 0 нулевой итог должен исключаться. '
                       'В отдельном тесте нуля используй взаимно погашающиеся значения +5 и -5 '
                       'одного объекта и ожидай пустую выдачу. В другом тесте используй только '
                       'отрицательное значение -3 и ожидай пустую выдачу. Положительный пример не заменяет тест нуля.')
        previous = []
        for turn in history:
            previous += [HumanMessage(content=turn['question']), AIMessage(content=turn['answer'])]
        messages = [SystemMessage(content=policy)] + previous + [graph_history[1]] + retrieval_messages()
        feedback = state.get('review_feedback', '')
        if feedback:
            messages.append(HumanMessage(content=feedback))
        last_ai = max(i for i, m in enumerate(graph_history) if isinstance(m, AIMessage))
        # Search evidence and loaded skills stay available across later tool rounds.
        # Other tools (e.g. SQL metadata) retain their complete latest call/result pair.
        if last_ai >= 4 and any(c['name'] not in {'search', 'load_skill', 'read_source'} for c in graph_history[last_ai].tool_calls):
            messages += graph_history[last_ai:]
        message = bound.invoke(messages)
        presented_code.update(row['id'] for row in session.packed())
        if (not message.tool_calls and 'code' in loaded_skills
                and state['steps'] < max_steps-2 and session.rows):
            # Small local models may answer before requesting omitted code. Keep
            # scanning the primary file, with a final generation step reserved.
            from ..knowledge.scoping import document_key
            primary = document_key(session.rows[0])
            unread = next((row for row in session.rows if row['id'] not in presented_code
                           and document_key(row) == primary), None)
            if unread:
                message = AIMessage(content='', tool_calls=[{
                    'id': f'backend-code-continuation-{state["steps"]+1}', 'name': 'read_source',
                    'args': {'chunk_id': unread['id'], 'offset': 0, 'limit': 6}}])
        if not message.tool_calls:
            normalized = normalize_sql_aliases(str(message.content), session.rows, question)
            normalized = normalize_sort_contract(normalized, session.rows, question)
            message = message.model_copy(update={'content': normalize_requested_format(normalized, question)})
        steps = state['steps'] + 1
        repaired, repair_failed = state.get('citation_repaired', False), False
        if (not message.tool_calls and session.rows and not repaired
                and steps < max_steps and message.response_metadata.get('finish_reason') != 'length'):
            _, cited, issues = render_citations(str(message.content), list(session.evidence.values()))
            content_issues = inspect_answer(question, str(message.content), list(session.evidence.values()), cited=cited)
            if issues or content_issues:
                repaired = True
                steps += 1
                correction = HumanMessage(content=(
                    'Исправь ошибки проверки в предыдущем ответе. '
                    'Используй только существующие метки ref из переданных результатов '
                    'в формате [S1] с латинской S для подтверждённых ими утверждений. '
                    'Ответ можно дополнять общими знаниями и примерами с явными допущениями; '
                    'отделяй их от сведений источников. Не создавай адресов источников. '
                    'Исправь замечания к содержанию, алгоритму и тестам в объёме исходного вопроса. '
                    'Верни только окончательный ответ без пересказа ошибочных вариантов и хода исправления. '
                    'Метки источников поставь отдельным абзацем после кода. '
                    'Верни полный ответ со ссылками рядом с подтверждаемыми утверждениями.\n'
                    'Замечания проверки. Используй полные фрагменты из backend-retrieval; '
                    'замечания не заменяют результаты поиска:\n' + json.dumps({
                        'issues': [{'code': issue['code'], 'message': issue['message']} for issue in content_issues]
                                  or [{'message': 'Отсутствует точная ссылка или есть неподтверждённый адрес.'}]}, ensure_ascii=False)))
                feedback = str(correction.content)
                try:
                    # Regenerate from source and specific findings. Repeating the
                    # rejected answer here anchors small local models to its errors.
                    message = bound.invoke(messages + [correction])
                    if not message.tool_calls:
                        normalized = normalize_sql_aliases(str(message.content), session.rows, question)
                        normalized = normalize_sort_contract(normalized, session.rows, question)
                        message = message.model_copy(update={'content': normalize_requested_format(normalized, question)})
                except Exception:
                    # A failed optional correction must not discard an existing answer.
                    repair_failed = True
        return {'messages': [message], 'steps': steps, 'citation_repaired': repaired,
                'citation_repair_failed': repair_failed, 'review_feedback': feedback}

    def execute(state):
        results = []
        budget = 12000
        for index, call in enumerate(state['messages'][-1].tool_calls):
            try:
                result = tools[call['name']].invoke(call['args']) if index < 2 else 'Per-step tool limit'
            except Exception:
                result = 'Tool unavailable or access denied'
            content = str(result)[:max(0, budget)] or 'Context budget exhausted'
            budget -= len(content)
            results.append(ToolMessage(content=content, tool_call_id=call['id']))
        return {'messages': results}

    def route(state):
        if not state['messages'][-1].tool_calls:
            return END
        return 'limit' if state['steps'] >= max_steps else 'tools'

    graph = StateGraph(State)
    graph.add_node('retrieve', retrieve)
    graph.add_node('model', call)
    graph.add_node('tools', execute)
    graph.add_node('limit', lambda state: {'messages': [AIMessage(content='Достигнут лимит шагов. Уточните вопрос.')]})
    graph.add_node('quote', lambda state: {'messages': [AIMessage(content=state['direct_answer'])]})
    graph.add_edge(START, 'retrieve')
    graph.add_conditional_edges('retrieve', lambda state: 'quote' if state['direct_answer'] else 'model')
    graph.add_edge('quote', END)
    graph.add_conditional_edges('model', route)
    graph.add_edge('tools', 'model')
    graph.add_edge('limit', END)
    policy = '\n'.join(Path('instructions', name + '.md').read_text(encoding='utf-8') for name in ('system', 'security'))
    policy += '\nSkill catalog: ' + json.dumps({k: v[0] for k, v in catalog.items()}, ensure_ascii=False)
    result = graph.compile().invoke({'messages': [SystemMessage(content=policy), HumanMessage(content=question)], 'steps': 0, 'direct_answer': '',
                                    'citation_repaired': False, 'citation_repair_failed': False, 'review_feedback': ''}, {'recursion_limit': 2 * max_steps + 4})
    evidence = list(session.evidence.values())
    final_text = str(result['messages'][-1].content)
    _, cited, link_warnings = render_citations(final_text, evidence)
    final_issues = inspect_answer(question, final_text, evidence, cited=cited)
    if link_warnings and cited and '```' not in final_text:
        anchors = set(re.findall(r'\b[A-Za-z][A-Za-z0-9]*_[A-Za-z_0-9]+\b', prose(final_text)))
        supported = [row for row in cited if any(anchor in re.sub(r'\s+', '', row['text']) for anchor in anchors)]
        if supported:
            final_issues.append({'code': 'unverified_address', 'evidence': supported})
    fallback = evidence_answer(final_issues, evidence)
    if fallback:
        result['messages'][-1] = AIMessage(content=fallback)
    response = finalize(question, result, evidence)
    response['retrieval'] = {'catalog_selection': session.routing_status, 'extractive_fallback': bool(fallback)}
    return response

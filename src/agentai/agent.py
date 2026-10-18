import json
from pathlib import Path
from typing import Annotated, TypedDict
import yaml
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from fastapi import HTTPException
from .core import require, sql_metadata, tokenize
from .retrieval import document_key, explicit_targets


class State(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int
    direct_answer: str


def skills():
    result = {}
    for path in Path('skills').glob('*/SKILL.md'):
        _, header, body = path.read_text(encoding='utf-8').split('---',2)
        meta = yaml.safe_load(header)
        result[meta['name']] = (meta['description'],body.strip())
    return result


def answer(question,user,store,model,max_steps=4,history=None):
    require(user,'reader')
    evidence = {}
    search_context = {'query':question[:2000],'rows':[]}
    loaded_skills = {}
    pinned_documents = set()
    scope_ready = {'active': False}
    catalog = skills()
    history = history or []
    followup = len(tokenize(question))<=12 and any(word in question.lower() for word in ('продолж','этот','этого','это','его','доработ','исправ','подробнее','сравни'))
    if followup and history and not any(explicit_targets(question)):
        previous = history[-1]
        pinned_documents.update(tuple(key) for key in previous.get('context',{}).get('selected',[]))
        scope_ready['active'] = bool(pinned_documents)
        search_context['query'] = (previous['question']+' '+question)[:2000]
    @tool
    def search(query: str) -> str:
        """Search authorized documents and code; return evidence and exact source locations."""
        require(user,'reader')
        rows = store.search(query[:2000],user['sources'])
        if scope_ready['active']:
            rows = [row for row in rows if document_key(row) in pinned_documents]
        for row in rows:
            evidence[row['id']] = row
        if rows:
            search_context.update(query=query[:2000],rows=rows)
        return json.dumps(rows,ensure_ascii=False)
    @tool
    def load_skill(name: str) -> str:
        """Load one skill's detailed instructions from the catalog."""
        body = catalog.get(name,('', 'Unknown skill'))[1][:4000]
        if name in catalog:
            loaded_skills[name] = body
        return body
    @tool
    def read_source(chunk_id: str, offset: int = -1, limit: int = 3) -> str:
        """Read neighboring chunks of an already retrieved source. Offset is relative to the chunk; use positive offsets to continue."""
        require(user,'reader')
        if chunk_id not in evidence: return 'Retrieve a source first'
        rows = store.read_source(chunk_id,user['sources'],offset,max(1,min(6,limit)))
        for row in rows: evidence[row['id']] = row
        if rows: search_context.update(rows=rows)
        return json.dumps(rows,ensure_ascii=False)
    @tool
    def database_schema() -> str:
        """Read SQL Server schema metadata only."""
        require(user,'sql_reader')
        return json.dumps(sql_metadata(),default=str)
    available = [search,load_skill,read_source]
    if 'sql_reader' in user['roles']:
        available.append(database_schema)
    tools = {t.name:t for t in available}
    bound = model.bind_tools(available)
    def retrieval_messages():
        call_id = 'backend-retrieval'
        rows, used = [],0
        for row in search_context['rows']:
            entry = dict(row)
            entry['text'] = entry['text'][:max(0,11000-used)]
            size = len(json.dumps(entry,ensure_ascii=False))
            if used+size>12000: break
            rows.append(entry); used += size
        return [AIMessage(content='',tool_calls=[{'id':call_id,'name':'search','args':{'query':search_context['query']}}]),
                ToolMessage(content=json.dumps(rows,ensure_ascii=False),tool_call_id=call_id)]
    def retrieve(state):
        # Finding authorized evidence is a backend guarantee, not a model decision.
        try:
            search.invoke({'query':search_context['query']})
            if hasattr(store,'read_source') and search_context['rows']:
                first = search_context['rows'][0]
                neighbors = store.read_source(first['id'],user['sources'],-1,3)
                known = {row['id'] for row in search_context['rows']}
                for row in neighbors:
                    if row['id'] not in known:
                        search_context['rows'].append(row); evidence[row['id']] = row
            if any(explicit_targets(question)):
                pinned_documents.update(document_key(row) for row in search_context['rows'])
                scope_ready['active'] = True
        except Exception:
            raise HTTPException(503,'Document search unavailable; check Qdrant and embeddings') from None
        phrase = tokenize(question)
        rows = search_context['rows']
        # An exact document heading is a lookup request; return source text verbatim.
        # This rule uses headings and ACL-filtered data, never project-specific names.
        titled = [row for row in rows if row['citation'].startswith('document:')
                  and 3 <= len(phrase) <= 16
                  and any(tokenize(line)==phrase for line in row['text'].splitlines()[:30])]
        direct = ''
        if titled:
            selected = [row for row in rows if any(
                row['id']==hit['id'] or (row.get('document_id') and row.get('document_id')==hit.get('document_id')
                                        and row.get('version')==hit.get('version') and row['source']==hit['source'])
                for hit in titled)]
            evidence.clear()
            evidence.update((row['id'],row) for row in selected)
            direct = 'Найден документ. Фрагмент исходного текста:\n\n'+'\n\n'.join(
                '\n'.join('> '+line for line in row['text'].splitlines())+'\n\nИсточник: '+row['citation']
                for row in selected)
        return {'messages':retrieval_messages(),'direct_answer':direct}
    def call(state):
        graph_history = state['messages']
        policy = graph_history[0].content
        if loaded_skills:
            policy += '\nAlready loaded skills:\n'+json.dumps(loaded_skills,ensure_ascii=False)
        previous = []
        for turn in history:
            previous += [HumanMessage(content=turn['question']),AIMessage(content=turn['answer'])]
        messages = [SystemMessage(content=policy)]+previous+[graph_history[1]]+retrieval_messages()
        last_ai = max(i for i,m in enumerate(graph_history) if isinstance(m,AIMessage))
        # Search evidence and loaded skills stay available across later tool rounds.
        # Other tools (e.g. SQL metadata) retain their complete latest call/result pair.
        if last_ai>=4 and any(c['name'] not in {'search','load_skill','read_source'} for c in graph_history[last_ai].tool_calls):
            messages += graph_history[last_ai:]
        return {'messages':[bound.invoke(messages)],'steps':state['steps']+1}
    def execute(state):
        results = []
        budget = 12000
        for index,call in enumerate(state['messages'][-1].tool_calls):
            try:
                result = tools[call['name']].invoke(call['args']) if index<2 else 'Per-step tool limit'
            except Exception:
                result = 'Tool unavailable or access denied'
            content = str(result)[:max(0,budget)] or 'Context budget exhausted'
            budget -= len(content)
            results.append(ToolMessage(content=content,tool_call_id=call['id']))
        return {'messages':results}
    def route(state):
        if not state['messages'][-1].tool_calls:
            return END
        return 'limit' if state['steps']>=max_steps else 'tools'
    graph = StateGraph(State)
    graph.add_node('retrieve',retrieve)
    graph.add_node('model',call)
    graph.add_node('tools',execute)
    graph.add_node('limit',lambda state:{'messages':[AIMessage(content='Достигнут лимит шагов. Уточните вопрос.')]})
    graph.add_node('quote',lambda state:{'messages':[AIMessage(content=state['direct_answer'])]})
    graph.add_edge(START,'retrieve')
    graph.add_conditional_edges('retrieve',lambda state:'quote' if state['direct_answer'] else 'model')
    graph.add_edge('quote',END)
    graph.add_conditional_edges('model',route)
    graph.add_edge('tools','model')
    graph.add_edge('limit',END)
    policy = '\n'.join(Path('instructions',name+'.md').read_text(encoding='utf-8') for name in ('system','security'))
    policy += '\nSkill catalog: '+json.dumps({k:v[0] for k,v in catalog.items()},ensure_ascii=False)
    result = graph.compile().invoke({'messages':[SystemMessage(content=policy),HumanMessage(content=question)],'steps':0,'direct_answer':''},{'recursion_limit':2*max_steps+4})
    final_message = result['messages'][-1]
    text = str(final_message.content)
    finish_reason = final_message.response_metadata.get('finish_reason', 'stop')
    if finish_reason == 'length':
        text += '\n\nОтвет оборван из-за лимита токенов. Код неполный; запросите меньшую часть задачи.'
    retrieved = list(evidence.values())
    cited = [row for row in retrieved if row['citation'] in text]
    warnings = []
    if any(row.get('validity')=='obsolete' or 'не актуально' in row['text'].lower() for row in retrieved):
        warnings.append('Среди материалов есть устаревшие документы; проверьте актуальность выводов.')
    if len({row.get('validity','current') for row in retrieved})>1:
        warnings.append('Источники имеют разный статус актуальности; возможны противоречия.')
    if retrieved and not cited and not result.get('direct_answer'):
        warnings.append('Ответ не содержит точных ссылок на доказательства; выводы требуют проверки.')
    if warnings: text += '\n\n'+'\n'.join(warnings)
    if cited: text += '\n\nSources:\n'+'\n'.join('- '+r['citation'] for r in cited)
    elif retrieved: text += '\n\nНайденные материалы (использование в ответе не подтверждено):\n'+'\n'.join('- '+r['citation'] for r in retrieved)
    return {'answer':text,'sources':retrieved,'used_sources':cited,'warnings':warnings,'steps':result['steps'],
            'selected_sources':[list(document_key(row)) for row in retrieved], 'finish_reason':finish_reason}

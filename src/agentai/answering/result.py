"""Present and validate a completed answer without invoking a model."""
from typing import TypedDict
from ..knowledge.scoping import document_key
from .citations import render_citations
from .validation import inspect_answer
from .session import Evidence
import re


def without_source_lists(text):
    # Only remove a generated bibliography at the end; preserve ordinary prose
    # and code, and let render_citations collect its used refs before removal.
    parts = re.split(r'(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$))',text)
    for index in range(0,len(parts),2):
        parts[index] = re.sub(r'(?im)^\s*(?:#{1,6}\s*)?(?:Sources|Источники)\s*:\s*\n(?:\s*[-*].*(?:\n|$))+\s*$', '',parts[index])
    return ''.join(parts).rstrip()


class AnswerResult(TypedDict):
    answer: str
    sources: list[Evidence]
    used_sources: list[Evidence]
    warnings: list[str]
    steps: int
    selected_sources: list
    finish_reason: str
    validation: dict


def finalize(question, result, retrieved) -> AnswerResult:
    final_message = result['messages'][-1]
    text = str(final_message.content)
    finish_reason = final_message.response_metadata.get('finish_reason', 'stop')
    if finish_reason == 'length':
        text += '\n\nОтвет оборван из-за лимита токенов. Код неполный; запросите меньшую часть задачи.'
    text, cited, warnings = render_citations(text, retrieved)
    text = without_source_lists(text)
    validation_issues = [] if result.get('direct_answer') else inspect_answer(question, str(final_message.content), retrieved,cited=cited)
    citation_issues = [{'code':'citation', 'message':warning} for warning in warnings]
    if retrieved and not cited and not result.get('direct_answer'):
        citation_issues.append({'code':'missing_citation', 'message':'Нет точной ссылки на найденный источник.'})
    if validation_issues:
        text = 'Ответ не прошёл проверку; следующие утверждения требуют уточнения.\n\n' + text
        warnings.extend('Проверка ответа: ' + issue['message'] for issue in validation_issues)
    validation = {'passed':not (validation_issues or citation_issues),
                  'issues':validation_issues + citation_issues,
                  'correction_attempted':result.get('citation_repaired',False),
                  'correction_failed':result.get('citation_repair_failed',False)}
    if result.get('citation_repair_failed'):
        warnings.append('Уточнение ответа недоступно; сохранён первоначальный ответ.')
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
            'selected_sources':[list(document_key(row)) for row in retrieved], 'finish_reason':finish_reason,
            'validation':validation}

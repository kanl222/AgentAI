"""Checks for selected contradictions, not a general asymptotic proof engine."""
import re


def inspect_complexity(question, text, nested):
    if 'сложност' not in question.lower() or not nested:
        return []
    issues = []
    text = text.replace('**','').replace('`','')
    global_items = bool(re.search(r'\bI\b[^.\n]{0,65}(?:общ\w*|всех|все\b)',text,re.I))
    claims = re.finditer(r'([^.!?\n]*?)O\s*\(([^)]+)\)',text)
    for claim in claims:
        lead, formula = claim.groups()
        total = bool(re.search(r'(?:общ\w*|итог\w*|временн\w*)\s+сложност|время выполнения|^\s*сложност',lead,re.I))
        if total and global_items and not re.search(r'\bI\b',formula):
            issues.append(('complexity_conflict',
                'Одна из общих оценок времени пропускает общее число позиций I. '
                'Удали несовместимую общую оценку; отдельно назови стоимости этапов, '
                'затем одну итоговую оценку времени и дополнительной памяти.'))
            break
    if global_items and re.search(r'O\s*\(\s*I\s*\)[^.!?\n]{0,40}на каждый',text,re.I):
        issues.append(('complexity_conflict',
            'I определено как общее число позиций, поэтому O(I) относится ко всему обходу. '
            'Для одного заказа используй его собственное число позиций; общий I повторно не умножай.'))
    memory_bound = re.search(r'(?:памят|пространств)[^.!?\n]{0,160}O\s*\('
                             r'|O\s*\([^)]+\)[^.!?\n]{0,50}(?:памят|пространств)',text,re.I)
    if not memory_bound and not re.search(r'только\s+врем',question,re.I):
        issues.append(('complexity_memory',
            'Отсутствует оценка дополнительной памяти. Укажи размеры создаваемых словарей '
            'и результата отдельно от времени и входных данных.'))
    return issues

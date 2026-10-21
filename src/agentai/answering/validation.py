"""Conservative checks of explicit claims against retrieved evidence.

These rules detect selected contradictions; passing is not a proof of truth.
"""
import re

from ..knowledge.scoping import explicit_targets
from .code_checks import checks as code_checks


def prose(text):
    text = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)', '', text)
    text = re.sub(r'^\s*>.*$', '', text, flags=re.M)
    text = re.sub(r'(?:document|gitlab):[^\s`<>]+|\[[SСC][0-9]+\]', '', text)
    return text.replace('**', '').replace('`', '')


def relation_values(text, labels, candidates):
    """Collect explicit labelled assignments, independently of their expected value."""
    text = prose(text)
    text = re.sub(r'\b(?:В\s+)?предыдущ\w*\s+ответ\w*[^.!?\n]*'
                  r'(?:ошибоч\w*|неверн\w*)[^.!?\n]*[.!?]?', '', text, flags=re.I)
    found = {label: set() for label in labels}
    label_lookup = {label.casefold(): label for label in labels}

    def values(part):
        # Source coordinates are references, not assignments to a column. Keep
        # the rest of the clause so a genuine second value is still rejected.
        part = re.sub(r'\b(?:таблиц\w*|строк\w*|страниц\w*|table|rows?|pages?)'
                      r'\s*№?\s*\d+(?:\s*[-–]\s*\d+)?', '', part, flags=re.I)
        return {value for value in candidates if any(
            not re.search(r'\b(?:не|not)\s*$',part[:match.start()],re.I)
            for match in re.finditer(r'(?<![\w])'+re.escape(value)+r'(?![\w])',part,re.I))}

    header = None
    for line in text.splitlines():
        if not line.strip():
            header = None
        if '|' not in line:
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        named = {i: label_lookup[c.casefold()] for i, c in enumerate(cells)
                 if c.casefold() in label_lookup}
        if len(named) == len(labels):
            header = named
            continue
        if cells and cells[0].casefold() in label_lookup:
            found[label_lookup[cells[0].casefold()]].update(values(' '.join(cells[1:])))
        elif header:
            for index, label in header.items():
                if index < len(cells):
                    found[label].update(values(cells[index]))

    plain = '\n'.join(line for line in text.splitlines() if '|' not in line)
    label_pattern = re.compile(r'(?<!\w)(?:' + '|'.join(
        re.escape(label) for label in sorted(labels, key=len, reverse=True)) + r')(?!\w)', re.I)
    for paragraph in re.split(r'\n\s*\n', plain):
        matches = list(label_pattern.finditer(paragraph))
        for index, match in enumerate(matches):
            end = matches[index+1].start() if index+1 < len(matches) else len(paragraph)
            found[label_lookup[match.group().casefold()]].update(values(paragraph[match.end():end]))
    return found


def _issue(code, message, rows, excerpts=None):
    return {'code': code, 'message': message,
            'evidence': [{'id': row['id'], 'ref': row.get('ref'), 'citation': row['citation'],
                          'text': (excerpts or {}).get(row['id'], row['text'])[:1800]}
                         for row in rows[:3]]}


def inspect_answer(question, text, rows):
    body = prose(text)
    issues = []
    absent_values = re.search(
        r'\bзначени\w*[^.!?\n]{0,100}(?:не\s+указан\w*|отсутств\w*)|'
        r'\bне\s+указан\w*[^.!?\n]{0,100}значени\w*', body, re.I)
    if absent_values:
        _, identifiers = explicit_targets(question)
        relevant = [row for row in rows if not identifiers or any(
            word.casefold() in re.sub(r'\s+', '', row['text']).casefold() for word in identifiers)]
        # An explicit list in the answer contradicts the absence claim. A known
        # field's list in evidence also disproves an unsupported absence claim.
        answer_list = re.search(r'значени\w*\s*[:—-][^.!?\n]*\d[^.!?\n]*\d', body, re.I)
        evidence_list = identifiers and any(len(re.findall(r'=\s*\d+\b', row['text'])) >= 2 for row in relevant)
        if answer_list or evidence_list:
            issues.append(_issue('contradictory_absence',
                'Противоречие: ответ утверждает, что значения не указаны, хотя они перечислены '
                'в ответе или в исходном фрагменте. Удали ложное отсутствие и перечисли значения по источнику.',
                relevant or rows))

    # Reconstructed table rows carry actual column headings. Only compare short
    # identifiers, or numeric properties with an explicit identifier as row name.
    for row in rows:
        for line in row['text'].splitlines():
            if not re.match(r'Таблица \d+, строка \d+:', line):
                continue
            cells = line.split(' | ')
            pairs = [cell.split(': ', 1) for cell in cells[1:] if ': ' in cell]
            expected = {label.strip(): value.strip() for label, value in pairs}
            numeric_property = bool(re.search(r': [A-Za-z_]\w*$', cells[0]))
            if len(expected) < 2 or len(set(expected.values())) < 2:
                continue
            if not all(re.fullmatch(r'[A-Za-z_*][\w.*:/()_-]{0,80}', value) or
                       (numeric_property and re.fullmatch(r'\d{1,10}', value)) for value in expected.values()):
                continue
            claims = relation_values(body, expected, set(expected.values()))
            wrong = {label: sorted(values) for label, values in claims.items()
                     if values and values != {expected[label]}}
            if wrong:
                issues.append(_issue('table_value_mismatch',
                    'Сопоставление значений со столбцами противоречит таблице или неоднозначно: '
                    + '; '.join(f'{label}: {", ".join(values)}; в источнике {expected[label]}'
                                for label, values in wrong.items()), [row], {row['id']: line}))
    issues.extend(_issue(code,message,rows) for code,message in code_checks(question,text,rows))
    # The same row can be repeated by neighboring chunks; keep feedback bounded.
    unique = {(issue['code'], issue['message']): issue for issue in issues}
    return list(unique.values())[:6]

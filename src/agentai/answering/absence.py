"""Narrow, source-backed signals for false absence claims."""
import re

from ..knowledge.text import lexical_tokens


def absence_issues(question, body, rows):
    comparison_denial = re.search(r'нет\s+прям\w*\s+сравнен|отсутств\w*[^.!?\n]{0,50}сравнител\w*\s+данн',body,re.I)
    meaningful={word for word in lexical_tokens(question) if len(word)>=3 and word not in
                {'система','системы','лучше','сравнение','преимущества','чем','наша','нашей'}
                and not word.startswith(('ru:систем','ru:сравне','ru:преиму'))}
    meaningful.update(word.casefold() for word in re.findall(r'\b[А-ЯЁ]{2,6}\b',question))
    comparison_rows = [row for row in rows if re.search(r'плюсы|преимущества|сравнение',row['text'][:1200],re.I)
                       and meaningful.intersection(lexical_tokens(row['text'][:1200]))]
    comparison = []
    if comparison_denial and comparison_rows and re.search(r'лучше|преимуществ|сравн|отлич',question,re.I):
        comparison.append(('unsupported_absence',
            'Источник содержит заявленные преимущества или сравнение. Перечисли их как утверждения '
            'документа; отдельно укажи отсутствие измерений, если их нет. Не отрицай наличие всех '
            'сравнительных сведений и не придумывай расшифровки сокращений.',comparison_rows))
    denial = re.search(r'\b(?:не\s+(?:содерж\w*|найден\w*|указан\w*)|нет|отсутств\w*)'
                      r'[^.!?\n]{0,100}(?:информац\w*|данн\w*|сведени\w*|определени\w*|инструкц\w*)|'
                      r'\b(?:информац\w*|данн\w*|сведени\w*)[^.!?\n]{0,70}не\s+найден\w*',body,re.I)
    if not denial:
        return comparison
    result = comparison
    definition = re.match(r'^\s*что\s+такое\s+(.+?)\s*[?!.]*$', question,re.I)
    if definition:
        terms = set(lexical_tokens(definition.group(1))) - {'_'}
        relevant = [row for row in rows if terms and terms <= set(lexical_tokens(row['text']))
                    and re.search(r'общее описание|наименование\s*:|\s[—–]\s',row['text'],re.I)]
        if relevant:
            result.append(('unsupported_absence',
                'В источнике найдено описание запрошенного термина, включая варианты его написания. '
                'Проверь текст и объясни найденное; отсутствие отдельной формулировки не означает '
                'отсутствие всех сведений о предмете.', relevant))
    if (re.match(r'^\s*как\b',question,re.I)
            and re.search(r'инструкц\w*',denial.group(),re.I)
            and len(re.findall(r'^\s*\d+[.)]\s+\S',body,re.M))>=2
            and re.search(r'однако[^.!?\n]*(?:опис|процесс|инструкц)',body,re.I)):
        result.append(('contradictory_absence',
            'Ответ отрицает наличие инструкции и затем сам приводит процесс по шагам. '
            'Если это запрошенная инструкция, убери ложный отказ; если действие другое, '
            'явно укажи различие и не выдавай его за ответ.', rows))
    return result

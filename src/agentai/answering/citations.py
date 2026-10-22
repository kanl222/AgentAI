"""Resolve model references against retrieved evidence; never trust generated URLs."""
import re


def render_citations(text, rows):
    references = {row['ref']: row for row in rows if row.get('ref')}
    locations = {row['citation']: row for row in rows}
    aliases = {citation.removeprefix('document:'): row for citation, row in locations.items()}
    allowed_urls = {url.rstrip('.,;') for row in rows
                    for url in re.findall(r'https?://[^\s<>"\x60)]+', row['text'])}
    used, issues = set(), set()

    def cite(row):
        used.add(row['id'])
        return row['citation']

    def marker(match):
        row = references.get('S' + match.group(1)[1:])
        if row:
            return cite(row)
        issues.add('Ответ содержит неподтверждённую метку источника.')
        return '[источник не подтверждён]'

    def link(match):
        label, destination = match.groups()
        ref = re.fullmatch(r'(?:(?:Источник|Ссылка на источник|Source)\s*:\s*)?[SСC]([1-9][0-9]*)', label, re.I)
        row = references.get('S' + ref.group(1)) if ref else None
        row = row or locations.get(label) or aliases.get(label)
        if row:
            return cite(row)
        if destination in locations:
            return cite(locations[destination])
        if destination.startswith(('http://', 'https://')) and destination not in allowed_urls:
            issues.add('Неподтверждённая ссылка модели удалена из ответа.')
            return label + ' [ссылка не подтверждена]'
        return match.group(0)

    # URLs in code examples are data, not claims of source provenance.
    parts = re.split(r'(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$))', text)
    for index in range(0, len(parts), 2):
        part = re.sub(r'\[([^\]\n]+)\]\(([^\s)]+)\)', link, parts[index])
        part = re.sub(r'\[(?:(?:Источник|Ссылка на источник|Source)\s*:\s*)?([SСC][1-9][0-9]*)\]', marker, part, flags=re.I)
        # Accept legacy exact locations, including the previously omitted prefix.
        for citation, row in locations.items():
            for location in {citation, citation.removeprefix('document:')}:
                pattern = r'(?<![\w:/])' + re.escape(location) + r'(?![\w?&#/=-])'
                part = re.sub(pattern, lambda match: cite(row), part)
        def location(match):
            value = match.group(0)
            candidate = value.rstrip('.,;')
            if candidate in locations:
                return cite(locations[candidate]) + value[len(candidate):]
            issues.add('Ответ содержит неподтверждённый адрес источника.')
            return '[источник не подтверждён]' + value[len(candidate):]
        part = re.sub(r'(?:document|gitlab|attachment):[^\s<>"\x60\[\]()]+', location, part)
        def url(match):
            value = match.group(0)
            if value.rstrip('.,;') in allowed_urls:
                return value
            issues.add('Неподтверждённая ссылка модели удалена из ответа.')
            return '[ссылка не подтверждена]'
        part = re.sub(r'https?://[^\s<>"\x60)\]]+', url, part)
        parts[index] = part
    return ''.join(parts), [row for row in rows if row['id'] in used], sorted(issues)

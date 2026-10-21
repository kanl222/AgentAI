"""PDF text with ruled table rows that retain their column headings."""
from io import BytesIO
import re


def cell_text(value):
    if value is None:
        return '[объединённая ячейка; отдельное значение не определено]'
    # Restore identifiers wrapped inside a cell, without joining prose or SQL.
    if '_' in value and re.fullmatch(r'[A-Za-z0-9_\n\r]+', value.strip()):
        return re.sub(r'\s+', '', value)
    return ' '.join(value.split())


def table_text(table, number):
    rows = table.extract()
    headers = [cell_text(cell) if cell else f'Столбец {i+1}'
               for i, cell in enumerate(rows[0])]
    lines = [f'Таблица {number}. Заголовки: ' + ' | '.join(headers)]
    for index, row in enumerate(rows[1:], 2):
        pairs = [f'{header}: {cell_text(value)}' for header, value in zip(headers, row)]
        lines.append(f'Таблица {number}, строка {index}: ' + ' | '.join(pairs))
    return '\n'.join(lines)


def pages(data):
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    with pdfplumber.open(BytesIO(data)) as pdf:
        for original, page in zip(reader.pages, pdf.pages):
            tables = [table for table in page.find_tables()
                      if len(table.rows) >= 2 and len(table.columns) >= 2]
            if not tables:
                # Keep ordinary text and SQL extraction unchanged.
                yield original.extract_text() or ''
                continue

            def outside_tables(obj):
                if obj.get('object_type') != 'char':
                    return True
                x = (obj['x0'] + obj['x1']) / 2
                y = (obj['top'] + obj['bottom']) / 2
                return not any(t.bbox[0] <= x <= t.bbox[2] and
                               t.bbox[1] <= y <= t.bbox[3] for t in tables)

            events = [(line['top'], line['x0'], line['text'])
                      for line in page.filter(outside_tables).extract_text_lines()]
            for number, table in enumerate(sorted(tables, key=lambda t: (t.bbox[1], t.bbox[0])), 1):
                events.append((table.bbox[1], table.bbox[0], table_text(table, number)))
            yield '\n'.join(text for _, _, text in sorted(events, key=lambda e: (e[0], e[1])))

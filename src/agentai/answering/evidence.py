"""Bounded model context that retains earlier successful retrievals."""
import json


def merge_evidence(current, previous):
    result, seen = [], set()
    for row in [*current, *previous]:
        if row['id'] not in seen:
            result.append(row)
            seen.add(row['id'])
    return result


def pack_evidence(rows, budget=12000, focus=()):
    """Share space across hits; explicitly mark clipping and omitted records.

    A read_source request receives priority, while other evidence keeps a preview.
    The budget counts serialized JSON including escaping, separators and metadata.
    """
    fields = ('id', 'ref', 'source', 'citation', 'document_id', 'version', 'validity', 'note', 'extraction_method')
    entries = []
    for row in rows[:12]:
        entry = {key: row[key] for key in fields if key in row}
        entry.update(text='', text_truncated=True)
        entries.append(entry)

    def size(value):
        return len(json.dumps(value, ensure_ascii=False))

    def annotate_omissions():
        if entries:
            entries[0].pop('context_omitted_count', None)
            if len(rows) > len(entries):
                entries[0]['context_omitted_count'] = len(rows) - len(entries)

    annotate_omissions()
    while entries and size(entries) > budget:
        entries.pop()
        annotate_omissions()
    remaining = budget - size(entries)
    order = sorted(range(len(entries)), key=lambda i: entries[i]['id'] not in focus)
    for position, index in enumerate(order):
        entry, original = entries[index], rows[index]['text']
        slots = len(order) - position
        allowance = (max(0, remaining - 512 * (slots - 1))
                     if entry['id'] in focus else remaining // slots)
        base = size(entry)
        low, high = 0, len(original)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = dict(entry, text=original[:middle], text_truncated=middle < len(original))
            if size(candidate) - base <= allowance:
                low = middle
            else:
                high = middle - 1
        entry.update(text=original[:low], text_truncated=low < len(original))
        remaining -= size(entry) - base
    return entries

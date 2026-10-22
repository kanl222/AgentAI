"""Bounded model context that retains earlier successful retrievals."""
import json


def merge_evidence(current, previous):
    result, seen = [], set()
    for row in [*current, *previous]:
        if row['id'] not in seen:
            result.append(row)
            seen.add(row['id'])
    return result


def pack_evidence(rows, budget=12000, focus=(), anchors=()):
    """Keep complete passages in priority order within the serialized JSON budget.

    Explicit reads come first. A passage is clipped only when even the first one
    cannot fit; omitted passages expose an ID that can be read in the next round.
    """
    fields = ('id', 'ref', 'source', 'citation', 'document_id', 'version', 'validity', 'note', 'extraction_method')
    ordered = [row for row in rows if row['id'] in focus] + [row for row in rows if row['id'] not in focus]

    def entry_for(row):
        entry = {key: row[key] for key in fields if key in row}
        entry.update(text=row['text'], text_truncated=False)
        return entry

    def size(value):
        return len(json.dumps(value, ensure_ascii=False))

    def annotated(entries):
        result = [dict(entry) for entry in entries]
        included = {entry['id'] for entry in result}
        omitted = [row for row in ordered if row['id'] not in included]
        if result and omitted:
            result[0].update(context_omitted_count=len(ordered)-len(result),
                             next_chunk_id=omitted[0]['id'])
        return result

    # Preserve the actual search hit from each expanded file before adding its
    # surroundings. A large early file must not hide the other candidate files.
    priority = (ordered if focus else [row for row in ordered if row['id'] in anchors]
                + [row for row in ordered if row['id'] not in anchors])
    positions = {row['id']: index for index, row in enumerate(ordered)}
    entries = []
    for row in priority:
        if len(entries) == 12:
            break
        candidate = sorted(entries + [entry_for(row)], key=lambda row: positions[row['id']])
        if size(annotated(candidate)) > budget:
            continue
        entries = candidate
    if entries or not ordered:
        return annotated(entries)

    # An unusually large single passage still gets an explicitly clipped preview.
    entry = entry_for(ordered[0])
    original = entry['text']
    entry.update(text='', text_truncated=True)
    if size(annotated([entry])) > budget:
        return []
    low, high = 0, len(original)
    while low < high:
        middle = (low + high + 1) // 2
        entry['text'] = original[:middle]
        if size(annotated([entry])) <= budget:
            low = middle
        else:
            high = middle - 1
    entry['text'] = original[:low]
    return annotated([entry])

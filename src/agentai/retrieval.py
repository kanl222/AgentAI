"""Deterministic narrowing for explicitly named files and project identifiers."""
import re


def document_key(row):
    """Group chunks of a version without merging independent documents."""
    return (row['source'], row['citation'].split('#', 1)[0].split('?', 1)[0])


def explicit_targets(query):
    files = re.findall(
        r'[\w.-]+\.(?:pdf|docx|txt|md|py|cs|java|js|ts|tsx|sql|go|rs)\b',
        query.lower(),
    )
    identifiers = [word for word in re.findall(r'\w+', query.lower()) if '_' in word]
    return files, identifiers


def scope_records(rows, query):
    """Narrow only explicit targets; semantic questions retain hybrid retrieval."""
    files, identifiers = explicit_targets(query)
    if not files and not identifiers:
        return rows, False
    if files:
        hits = [row for row in rows if any(
            re.search(r'(?<![\w.-])' + re.escape(name) + r'(?=[@?#]|$)', row['citation'].lower())
            for name in files
        )]
        # Source bundles may contain named source files inside document text.
        if not hits:
            hits = [row for row in rows if any(name in row['text'].lower() for name in files)]
    else:
        hits = [row for row in rows if set(identifiers) & set(re.findall(r'\w+', row['text'].lower()))]
    keys = {document_key(row) for row in hits}
    return [row for row in rows if document_key(row) in keys], True

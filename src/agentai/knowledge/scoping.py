"""Deterministic narrowing for explicitly named files and project identifiers."""
import re


FILE_PATTERN = re.compile(
    r'[\w.-]+\.(?:pdf|docx|txt|md|py|cs|java|js|ts|tsx|sql|go|rs|'
    r'php|jsx|vue|kt|rb|fs|vb|cpp|c|hpp|h|sh|yaml|yml|json|xml|toml)\b'
)


def document_key(row):
    """Group chunks of a version without merging independent documents."""
    return (row['source'], row['citation'].split('#', 1)[0].split('?', 1)[0])


def explicit_targets(query):
    files = FILE_PATTERN.findall(query.lower())
    identifiers = [word for word in re.findall(r'\w+', FILE_PATTERN.sub(' ', query.lower())) if '_' in word]
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
            hits = [row for row in rows if any(
                re.search(r'(?<![\w.-])' + re.escape(name) + r'(?![\w.-])', row['text'].lower())
                for name in files)]
    else:
        # PDF table cells can wrap an identifier: flag_all_\nstore. Match
        # whitespace inside the requested name without altering source text.
        patterns = [re.compile(r'(?<!\w)' + r'\s*'.join(map(re.escape, word)) + r'(?!\w)')
                    for word in identifiers]
        hits = [row for row in rows if any(pattern.search(row['text'].lower()) for pattern in patterns)]
    keys = {document_key(row) for row in hits}
    return [row for row in rows if document_key(row) in keys], True

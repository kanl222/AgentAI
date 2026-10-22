"""Private message attachments: extraction and request-local passage search.

No attachment is written to the shared document archive or vector index.
"""
import base64
import binascii
import uuid
from pathlib import Path

from ..errors import ApplicationError
from ..ingestion.extract import document, chunks
from ..knowledge.text import lexical_tokens
from ..knowledge.scoping import document_key

SOURCE = '__chat_attachment__'
TEXT_TYPES = {'.txt', '.md', '.py', '.sql', '.js', '.ts', '.tsx', '.jsx', '.json',
              '.yaml', '.yml', '.xml', '.html', '.css', '.cs', '.java', '.php', '.go', '.rs', '.sh', '.toml'}
TYPES = TEXT_TYPES | {'.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'}


def extract_attachments(attachments):
    rows, names, size, chars = [], [], 0, 0
    for attachment in attachments:
        name = attachment.filename.replace('\\', '/').rsplit('/', 1)[-1]
        suffix = Path(name).suffix.lower()
        if not name or any(ord(c) < 32 for c in name) or suffix not in TYPES:
            raise ApplicationError(422, 'Неподдерживаемый формат вложения: ' + name)
        try:
            data = base64.b64decode(attachment.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ApplicationError(422, 'Повреждены данные вложения: ' + name) from None
        size += len(data)
        if size > 15_000_000:
            raise ApplicationError(413, 'Общий размер вложений — не более 15 МБ.')
        identity = uuid.uuid4().hex
        location = f'attachment:{identity}/{name}'
        try:
            extracted = (chunks(data.decode('utf-8-sig'), SOURCE, location) if suffix in TEXT_TYPES
                         else document(data, name, SOURCE))
            count = 0
            for row in extracted:
                chars += len(row['text'])
                if chars > 400_000:
                    raise ApplicationError(413, 'Во вложениях слишком много текста. Разделите файлы на части.')
                row = dict(row, document_id=identity, attachment=True, filename=name)
                if row['citation'].startswith('document:'):
                    row['citation'] = row['citation'].replace(f'document:{SOURCE}/{name}', location, 1)
                row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, identity + row['citation'] + row['text']))
                rows.append(row)
                count += 1
            if not count:
                raise ApplicationError(422, 'Не удалось прочитать текст файла ' + name + '. Для сканов необходимо включить OCR.')
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError(422, 'Не удалось прочитать файл ' + name + '. Проверьте формат и содержимое.') from None
        names.append({'filename': name, 'size': len(data)})
    return rows, names


class AttachmentStore:
    """The normal answer engine searches only this message's attached files."""
    def __init__(self, rows):
        self.rows = rows

    def search(self, query, sources):
        if SOURCE not in sources:
            return []
        terms = set(lexical_tokens(query))
        ranked = sorted(self.rows, key=lambda row: len(terms & set(lexical_tokens(row['text']))), reverse=True)
        return ranked[:12]

    def read_source(self, chunk_id, sources, offset=-1, limit=3):
        anchor = next((row for row in self.rows if row['id'] == chunk_id), None)
        if not anchor or SOURCE not in sources:
            return []
        group = [row for row in self.rows if document_key(row) == document_key(anchor)]
        start = max(0, group.index(anchor) + offset)
        return group[start:start + limit]

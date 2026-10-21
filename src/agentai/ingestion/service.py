"""Application operation, callable from HTTP or a background worker."""
import base64
import uuid
from pathlib import Path
from ..access import require
from ..errors import ApplicationError
from ..ingestion.extract import document


@require('indexer', source=lambda body, **kw: body.source)
def save_upload(body, p, *, archive):
    try:
        data = base64.b64decode(body.content_base64, validate=True)
        filename = body.filename.replace('\\', '/').rsplit('/', 1)[-1]
        if not filename or any(ord(c) < 32 for c in filename):
            raise ValueError('Invalid filename')
        if Path(filename).suffix.lower() not in {'.pdf', '.docx', '.txt', '.md', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'}:
            raise ValueError('Unsupported document type')
        if len(data) > 20_000_000:
            raise ValueError('Maximum file size: 20 MB')
    except ValueError as error:
        raise ApplicationError(422, str(error)) from None
    identity = body.document_id or uuid.uuid5(uuid.NAMESPACE_URL, body.source + '/' + filename).hex
    return archive().save(body.source, identity, filename, data, p['name'])


def index_version(version, p, progress=None, *, archive, services):
    catalog = archive()
    item = catalog.get(version, p['sources'])
    if not item:
        raise ApplicationError(404, 'Document not found')
    require(p, 'indexer', item['source'])
    try:
        rows = list(document(catalog.path(version).read_bytes(), item['filename'], item['source']))
        if not rows:
            raise ValueError('No extractable text; scanned PDFs require OCR')
        for row in rows:
            row.update(document_id=item['document_id'], version=version, download_url=f'/documents/{version}/original')
            row['citation'] = row['citation'].replace('document:' + item['source'] + '/' + item['filename'],
                                                     'document:' + item['source'] + '/' + item['filename'] + '@' + version, 1)
            row['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, version + row['id']))
        if catalog.database and any('не актуально' in row['text'].lower() for row in rows):
            catalog.metadata(version, 'obsolete', 'В тексте есть отметка «Не актуально»')
        options = {'document_id': item['document_id']}
        if progress:
            options['progress'] = progress
        count = services()[0].replace(item['source'], rows, **options)
        if not catalog.database:
            catalog.finish(version, True)
        return {'chunks': count, 'document_id': item['document_id'], 'version': version, 'download_url': f'/documents/{version}/original'}
    except Exception:
        catalog.finish(version, False)
        raise ApplicationError(503, 'Original saved, indexing failed; retry after checking dependencies') from None

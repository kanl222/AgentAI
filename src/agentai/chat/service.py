"""Application operation, callable from HTTP or a background worker."""
from ..access import require
from .attachments import AttachmentStore, extract_attachments, SOURCE


@require('reader')
def run_chat(body, p, *, services, memory=None, answer, max_steps=4, job_id=None):
    store, model = services()
    identity = str(body.conversation_id) if body.conversation_id else None
    history = []
    if memory:
        if identity:
            history = memory.get(identity, p)['turns']
            referenced = {row['id'] for turn in history for row in turn['sources'] if row['source'] != SOURCE}
            active_ids = ((store.visible_ids(referenced, p['sources']) if hasattr(store, 'visible_ids')
                          else {point.payload['id'] for point in store.corpus(p['sources'])}) if referenced else set())
            history = [turn for turn in history if all(row['source'] == SOURCE or row['id'] in active_ids for row in turn['sources'])]
        else:
            identity = memory.create(p)
    options = {'max_steps': max_steps}
    if history:
        options['history'] = history
    rows, attachments = extract_attachments(getattr(body, 'attachments', []))
    # Reuse the previous file only for an explicit follow-up; an unrelated
    # question must keep the ordinary shared-document search behavior.
    if not rows and history:
        import re
        if re.search(r'\b(?:прикрепл\w*|вложен\w*|в этом файле|по этому файлу|в этом документе|продолжи|подробнее)\b', body.question, re.I):
            rows = history[-1].get('context', {}).get('attachment_rows', [])
    answer_user = dict(p, sources=[*p['sources'], SOURCE]) if rows else p
    result = answer(body.question, answer_user, AttachmentStore(rows) if rows else store, model, **options)
    if rows:
        result['attachment_rows'] = rows
    if attachments:
        result['attachments'] = attachments
    if memory:
        result['conversation_id'] = identity
        if job_id:
            result['access_sources'] = p['sources']
            result = memory.append(identity, p, body.question, result, job_id=job_id)
        else:
            memory.append(identity, p, body.question, result)
    return result

"""Application operation, callable from HTTP or a background worker."""
from ..access import require
from ..errors import ApplicationError
from ..adapters.gitlab import gitlab
from ..commands import Chat
from ..execution.queue import Jobs

def dispatch_job(job, p, progress, *, application):
    payload = job['payload']
    if job['kind']=='chat':
        require(p, 'reader')
        saved = Jobs(application.database()).get(str(job['id']), p)
        if saved['state'] == 'succeeded':
            if saved['result'] is None:
                raise ApplicationError(403, 'Access denied')
            return saved['result']
        result = application.run_chat(Chat(**payload),p,job_id=str(job['id']))
        result['access_sources'] = p['sources']
        return result
    require(p,'indexer',job['source'])
    if job['kind']=='index': return application.index_version(payload['version'],p,progress)
    if job['kind']=='gitlab':
        return {'chunks':application.services()[0].replace(job['source'],gitlab(job['source']),progress=progress)}
    if job['kind']=='remove':
        item = application.archive().get(payload['version'],p['sources'])
        if not item: raise ApplicationError(404,'Document not found')
        if item['state']=='removed':
            return {'removed_from_search':True,'original_retained':True}
        if item['state']!='indexed': raise ApplicationError(409,'Only current indexed version can be removed')
        application.services()[0].remove(item['source'],item['document_id'],version=item['version'])
        application.archive().remove_from_search(item['version'])
        return {'removed_from_search':True,'original_retained':True}
    raise ValueError('Unknown job kind')

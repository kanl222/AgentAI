"""Application operation, callable from HTTP or a background worker."""
from ..access import require

def run_chat(body, p, *, services, memory=None, answer, max_steps=4, job_id=None):
    require(p,'reader')
    store,model = services()
    identity = str(body.conversation_id) if body.conversation_id else None
    history = []
    if memory:
        if identity:
            history = memory.get(identity,p)['turns']
            referenced = {row['id'] for turn in history for row in turn['sources']}
            active_ids = (store.visible_ids(referenced, p['sources']) if hasattr(store, 'visible_ids')
                          else {point.payload['id'] for point in store.corpus(p['sources'])})
            history = [turn for turn in history if all(row['id'] in active_ids for row in turn['sources'])]
        else:
            identity = memory.create(p)
    options = {'max_steps':max_steps}
    if history: options['history'] = history
    result = answer(body.question,p,store,model,**options)
    if memory:
        result['conversation_id'] = identity
        if job_id:
            result['access_sources'] = p['sources']
            result = memory.append(identity,p,body.question,result,job_id=job_id)
        else:
            memory.append(identity,p,body.question,result)
    return result

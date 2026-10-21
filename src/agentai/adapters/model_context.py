"""Read LM Studio load configuration without loading or unloading models."""


def context_status(client, base_url, api_key, model_id, desired):
    base_url = base_url.rstrip('/')
    if not base_url.endswith('/v1'):
        raise ValueError('LMSTUDIO_BASE_URL must end with /v1 for context diagnostics')
    response = client.get(base_url[:-3] + '/api/v1/models',
                          headers={'Authorization': 'Bearer ' + api_key})
    response.raise_for_status()
    models = response.json()['models']
    # Prefer the exact instance ID used by OpenAI requests, then the model key.
    instances = [instance for model in models for instance in model['loaded_instances']
                 if instance['id'] == model_id]
    if not instances:
        instances = [instance for model in models if model['key'] == model_id
                     for instance in model['loaded_instances']]
    lengths = [instance['config']['context_length'] for instance in instances]
    if any(type(value) is not int or value < 1 for value in lengths):
        raise ValueError('Invalid context length')
    state = 'not_loaded' if not lengths else 'matched' if all(value == desired for value in lengths) else 'mismatch'
    return {'configured': desired, 'loaded': lengths, 'status': state}

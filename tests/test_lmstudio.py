import httpx
import pytest
from langchain_core.messages import HumanMessage
from agentai.api import lmstudio_clients


def test_lmstudio_chat_tools_and_embeddings(monkeypatch):
    monkeypatch.setenv('LMSTUDIO_BASE_URL','http://localhost:1234/v1')
    monkeypatch.setenv('LMSTUDIO_API_KEY','local-test-token')
    monkeypatch.setenv('CHAT_MODEL','test-qwen')
    monkeypatch.setenv('EMBED_MODEL','test-embed')
    seen=[]
    def handler(request):
        import json
        body=json.loads(request.content)
        assert request.headers['Authorization']=='Bearer local-test-token'
        seen.append(request.url.path)
        if request.url.path=='/v1/embeddings':
            assert body['model']=='test-embed'
            assert body['input']==['example text']
            return httpx.Response(200,json={'object':'list','model':'test-embed','data':[{'object':'embedding','index':0,'embedding':[1.,0.,0.]}],'usage':{'prompt_tokens':2,'total_tokens':2}})
        assert request.url.path=='/v1/chat/completions'
        assert body['model']=='test-qwen'
        assert body['tools'][0]['function']['name']=='search'
        return httpx.Response(200,json={'id':'local-test','object':'chat.completion','created':1,'model':'test-qwen','choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':'search','arguments':'{"query":"owner"}'}}]}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        embeddings,model=lmstudio_clients(client)
        assert embeddings.embed_query('example text')==[1.,0.,0.]
        response=model.bind_tools([{'type':'function','function':{'name':'search','description':'Search','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}}]).invoke([HumanMessage(content='owner?')])
        assert response.tool_calls[0]['args']=={'query':'owner'}
    assert seen==['/v1/embeddings','/v1/chat/completions']


@pytest.mark.parametrize('overrides, expected', [
    ({}, {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'min_p': 0,
          'presence_penalty': 0, 'max_completion_tokens': 16384}),
    ({'CHAT_TEMPERATURE': '0.25', 'CHAT_TOP_P': '0.95', 'CHAT_TOP_K': '40',
      'CHAT_MIN_P': '0.05', 'CHAT_PRESENCE_PENALTY': '0.5', 'CHAT_MAX_TOKENS': '2048'},
     {'temperature': 0.25, 'top_p': 0.95, 'top_k': 40, 'min_p': 0.05,
      'presence_penalty': 0.5, 'max_completion_tokens': 2048}),
])
def test_sampling_configuration_reaches_lmstudio(monkeypatch, overrides, expected):
    for name in ('CHAT_TEMPERATURE', 'CHAT_TOP_P', 'CHAT_TOP_K', 'CHAT_MIN_P',
                 'CHAT_PRESENCE_PENALTY', 'CHAT_MAX_TOKENS'):
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://localhost:1234/v1')
    monkeypatch.setenv('LMSTUDIO_API_KEY', 'local-test-token')
    received = []

    def handler(request):
        import json
        body = json.loads(request.content)
        received.append(body)
        for name, value in expected.items():
            assert body[name] == value
        assert 'extra_body' not in body
        assert body['tools'][0]['function']['name'] == 'ping'
        return httpx.Response(200, json={'id': 'sampling-test', 'object': 'chat.completion',
            'created': 1, 'model': body['model'], 'choices': [{'index': 0,
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, model = lmstudio_clients(client)
        model.bind_tools([{'type': 'function', 'function': {'name': 'ping',
            'description': 'Ping', 'parameters': {'type': 'object', 'properties': {}}}}]).invoke(
                [HumanMessage(content='Ping')])
    assert len(received) == 1


@pytest.mark.parametrize('value', ['NaN', 'inf', '-inf'])
def test_sampling_rejects_non_finite_values(monkeypatch, value):
    from agentai.settings import ModelSettings
    monkeypatch.setenv('CHAT_TEMPERATURE', value)
    with pytest.raises(ValueError, match='CHAT_TEMPERATURE must be finite'):
        ModelSettings.from_env()


def test_qwen_embedding_endpoint_and_query_instruction_are_separate_from_chat(monkeypatch):
    import json
    monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://chat/v1')
    monkeypatch.setenv('LMSTUDIO_API_KEY', 'chat-private')
    monkeypatch.setenv('EMBED_BASE_URL', 'http://cpu/v1')
    monkeypatch.setenv('EMBED_API_KEY', 'embedding-private')
    monkeypatch.setenv('EMBED_MODEL', 'qwen3-embedding-0.6b')
    monkeypatch.setenv('EMBED_QUERY_INSTRUCTION', 'Retrieve relevant passages')
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append((str(request.url), body))
        if request.url.host == 'cpu':
            assert request.headers['authorization'] == 'Bearer embedding-private'
            return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1., .5]} for i in range(len(body['input']))],
                                            'usage': {'prompt_tokens': 2, 'total_tokens': 2}})
        assert request.headers['authorization'] == 'Bearer chat-private'
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1,
            'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        embeddings, model = lmstudio_clients(client)
        assert embeddings.embed_documents(['Original document']) == [[1., .5]]
        assert embeddings.embed_query('Question?') == [1., .5]
        model.invoke([HumanMessage(content='Ping')])
    assert seen[0][1]['input'] == ['Original document']
    assert seen[1][1]['input'] == ['Instruct: Retrieve relevant passages\nQuery:Question?']
    assert seen[2][0] == 'http://chat/v1/chat/completions'

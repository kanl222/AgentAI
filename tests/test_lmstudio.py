import httpx
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

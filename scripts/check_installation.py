"""Check current configuration and real LM Studio APIs without printing secrets."""
import os
from pathlib import Path


def load_env():
    for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            name,value=line.split('=',1)
            value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":
                value=value[1:-1]
            os.environ[name.strip()]=value


if __name__=='__main__':
    load_env()
    from fastapi.testclient import TestClient
    from agentai.api import app,lmstudio_clients
    from langchain_core.messages import HumanMessage
    key=os.environ['WEBUI_READER_KEY']
    result=TestClient(app).get('/v1/models',headers={'Authorization':'Bearer '+key})
    print('Current source authenticated models:',result.status_code,flush=True)
    assert result.status_code==200
    assert result.json()['data'][0]['id']=='local-agent'
    embeddings,model=lmstudio_clients()
    vector=embeddings.embed_query('connection check')
    print('Real LM Studio embeddings dimensions:',len(vector),flush=True)
    model.max_tokens=96
    response=model.bind_tools([{'type':'function','function':{'name':'ping','description':'Check connection','parameters':{'type':'object','properties':{}}}}]).invoke([HumanMessage(content='Call the ping tool once to test the connection. Do not explain.')])
    print('Real LM Studio tool calls:',[call['name'] for call in response.tool_calls],flush=True)
    assert any(call['name']=='ping' for call in response.tool_calls),'Model did not call the tool'
    print('Configuration, embeddings and tool calling OK',flush=True)

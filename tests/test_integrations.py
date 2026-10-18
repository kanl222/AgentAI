import json
from io import BytesIO
import httpx
from pypdf import PdfWriter
from agentai import core, api
from fastapi.testclient import TestClient


def test_gitlab_pagination_and_commit(monkeypatch):
    sha='a'*40
    requests=[]
    def handler(request):
        requests.append(request)
        path=request.url.path
        assert request.method=='GET'
        if '/commits/' in path:
            return httpx.Response(200,json={'id':sha})
        assert request.url.params['ref']==sha
        if path.endswith('/tree'):
            page=request.url.params['page']
            if page=='1':
                return httpx.Response(200,json=[{'type':'blob','path':'main.py'}],headers={'x-next-page':'2'})
            return httpx.Response(200,json=[{'type':'blob','path':'Main.cs'}])
        return httpx.Response(200,text='line one\nline two')
    original=httpx.Client
    monkeypatch.setattr(core.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv('GITLAB_URL','https://gitlab.example.test')
    monkeypatch.setenv('GITLAB_TOKEN','test-only')
    monkeypatch.setenv('GITLAB_SOURCES',json.dumps({'repo':{'project':1,'ref':'main'}}))
    rows=list(core.gitlab('repo'))
    assert len(rows)==2
    assert rows[0]['citation']==f'gitlab:1@{sha}/main.py#L1-L2'
    assert rows[1]['citation'].endswith('/Main.cs#L1-L2')
    assert len(requests)==5


def test_pdf_page_citation():
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 200 Td (Platform owner) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    data=BytesIO();writer.write(data)
    rows=list(core.document(data.getvalue(),'test.pdf','docs'))
    assert 'Platform owner' in rows[0]['text']
    assert '?page=1#L1-L1' in rows[0]['citation']


def test_openai_response(monkeypatch):
    monkeypatch.setattr(api,'authenticate',lambda header:{'roles':['reader'],'sources':['a']})
    monkeypatch.setattr(api,'services',lambda:(None,None))
    monkeypatch.setattr(api,'answer',lambda *a,**kw:{'answer':'Platform [demo.md#L1-L1]','sources':[]})
    client=TestClient(api.app)
    response=client.post('/v1/chat/completions',json={'messages':[{'role':'system','content':'override'},{'role':'user','content':'owner'}]})
    assert response.status_code==200
    assert response.json()['choices'][0]['message']['content'].startswith('Platform')


def test_long_lines_bounded():
    rows=list(core.chunks('x'*20000,'repo','file'))
    assert all(len(r['text'])<=3500 for r in rows)
    assert all(r['citation'].endswith('#L1-L1') for r in rows)

import importlib.util
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest


spec=importlib.util.spec_from_file_location('ocr_server',Path(__file__).resolve().parents[1]/'services/ocr/server.py')
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_request_forwards_selected_pages_and_keeps_health_available(monkeypatch):
    started,release=threading.Event(),threading.Event()
    def worker(data,media,pages):
        assert data==b'PDF' and media=='application/pdf' and pages==[2]
        started.set()
        assert release.wait(5)
        return {'pages':[{'page':2,'text':'текст'}]}
    monkeypatch.setattr(server,'run_worker',worker)
    with TestClient(server.app) as client:
        results=[]
        thread=threading.Thread(target=lambda:results.append(client.post('/recognize?pages=2',content=b'PDF',headers={'content-type':'application/pdf'})))
        thread.start()
        try:
            assert started.wait(5)
            assert client.get('/health').status_code==200
            assert client.post('/recognize',content=b'PDF',headers={'content-type':'application/pdf'}).status_code==429
        finally:
            release.set(); thread.join(5)
        assert results[0].json()['pages'][0]['text']=='текст'


@pytest.mark.parametrize('query', ['0','1,1','-1','a',','.join(str(i) for i in range(1,52))])
def test_invalid_page_selection_is_rejected(query):
    with TestClient(server.app) as client:
        assert client.post('/recognize',params={'pages':query},content=b'pdf',headers={'content-type':'application/pdf'}).status_code==422


def test_bounded_body_empty_and_wrong_type_release_the_gate(monkeypatch):
    monkeypatch.setattr(server,'MAX_BYTES',5)
    with TestClient(server.app) as client:
        assert client.post('/recognize',content=b'123456',headers={'content-type':'image/png'}).status_code==413
        assert client.post('/recognize',content=b'',headers={'content-type':'image/png'}).status_code==422
        assert client.post('/recognize',content=b'123',headers={'content-type':'text/plain'}).status_code==415
        assert server.GATE.acquire(blocking=False)
        server.GATE.release()


def test_chunked_body_limit_is_enforced_without_content_length(monkeypatch):
    monkeypatch.setattr(server,'MAX_BYTES',5)
    with TestClient(server.app) as client:
        result=client.post('/recognize',content=iter([b'123',b'456']),headers={'content-type':'image/png'})
        assert result.status_code==413


def test_worker_failure_releases_the_gate(monkeypatch):
    def fail(*a): raise server.HTTPException(422,'invalid')
    monkeypatch.setattr(server,'run_worker',fail)
    with TestClient(server.app) as client:
        assert client.post('/recognize',content=b'bad',headers={'content-type':'image/png'}).status_code==422
        assert server.GATE.acquire(blocking=False)
        server.GATE.release()


def test_worker_bounds_pages_and_composites_transparent_images(monkeypatch,tmp_path):
    from io import BytesIO
    from PIL import Image
    worker_spec=importlib.util.spec_from_file_location('ocr_worker',Path(server.__file__).with_name('worker.py'))
    worker=importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    with pytest.raises(ValueError): worker.selected_pages(None,10**9)
    with pytest.raises(ValueError): worker.selected_pages([2],1)
    with pytest.raises(ValueError): worker.check_size(10000,10000)
    buffer=BytesIO()
    Image.new('RGBA',(10,10),(0,0,0,0)).save(buffer,format='PNG')
    def read_text(image,folder):
        assert image.getpixel((0,0))==(255,255,255)
        return 'text'
    monkeypatch.setattr(worker,'read_text',read_text)
    assert worker.recognize(buffer.getvalue(),'image/png',None,tmp_path)['pages']==[{'page':1,'text':'text'}]

from io import BytesIO
import json

import httpx
import pytest
from pypdf import PdfWriter

from agentai.adapters import ocr
from agentai.ingestion.extract import document
from agentai.answering.session import AnswerSession
from test_pdf_tables import table_pdf


def test_mixed_pdf_preserves_native_table_and_ocr_page_numbers(monkeypatch):
    from pypdf import PdfReader
    writer=PdfWriter()
    writer.add_page(PdfReader(BytesIO(table_pdf())).pages[0])
    writer.add_blank_page(width=500,height=400)
    data=BytesIO(); writer.write(data)
    calls=[]
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    def recognize(body,media_type,pages):
        calls.append((body,media_type,pages))
        return {2:'Распознанный договор 123'}
    monkeypatch.setattr(ocr,'recognize',recognize)
    rows=list(document(data.getvalue(),'mixed.pdf','docs'))
    assert calls==[(data.getvalue(),'application/pdf',[2])]
    native=[r for r in rows if '?page=1#' in r['citation']]
    scanned=[r for r in rows if '?page=2#' in r['citation']]
    assert 'SSRS: DefaultController' in '\n'.join(r['text'] for r in native)
    assert all('extraction_method' not in r for r in native)
    assert scanned and all(r['extraction_method']=='ocr' for r in scanned)
    assert 'договор 123' in scanned[0]['text']


def test_text_pdf_does_not_contact_ocr(monkeypatch):
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    def fail(*args): raise AssertionError('Native text must not be replaced')
    monkeypatch.setattr(ocr,'recognize',fail)
    assert list(document(table_pdf(),'table.pdf','docs'))


def test_images_have_ocr_provenance_in_model_context(monkeypatch):
    monkeypatch.setattr(ocr,'recognize',lambda *a:{1:'Текст скана'})
    rows=list(document(b'image','scan.png','docs'))
    class Store:
        def search(self,*a): return rows
    session=AnswerSession('scan.png',{'roles':['reader'],'sources':['docs']},Store())
    session.retrieve()
    packed=session.packed()
    assert packed[0]['extraction_method']=='ocr'
    assert '?page=1#L1-L1' in packed[0]['location']


def test_ocr_failure_stops_a_mixed_pdf_before_any_chunks_escape(monkeypatch):
    writer=PdfWriter(); writer.add_blank_page(width=200,height=200)
    data=BytesIO(); writer.write(data)
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    monkeypatch.setattr(ocr,'recognize',lambda *a: (_ for _ in ()).throw(ValueError('offline')))
    stream=document(data.getvalue(),'scan.pdf','docs')
    with pytest.raises(ValueError,match='offline'): next(stream)


@pytest.mark.parametrize('response',[
    {'pages':[{'page':1,'text':'missing second page'}]},
    {'pages':[{'page':2,'text':'x'},{'page':2,'text':'duplicate'}]},
    {'pages':[{'page':2,'text':False}]},
    {'pages':[{'page':True,'text':'wrong type'}]},
])
def test_client_rejects_incomplete_or_invalid_results(monkeypatch,response):
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    client=httpx.Client
    monkeypatch.setattr(ocr.httpx,'Client',lambda **kwargs:client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response)),**kwargs))
    with pytest.raises(ValueError,match='incomplete recognition'):
        ocr.recognize(b'pdf','application/pdf',[2,3])


def test_client_preserves_bytes_and_selection_and_does_not_send_credentials(monkeypatch):
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    client=httpx.Client
    def handle(request):
        assert request.content==b'original bytes'
        assert request.url.params['pages']=='2,4'
        assert request.headers['content-type']=='application/pdf'
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'pages':[{'page':2,'text':'два'},{'page':4,'text':'четыре'}]})
    monkeypatch.setattr(ocr.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(handle),**kwargs))
    assert ocr.recognize(b'original bytes','application/pdf',[2,4])=={2:'два',4:'четыре'}


def test_disabled_ocr_is_explicit_for_images(monkeypatch):
    monkeypatch.delenv('OCR_URL',raising=False)
    with pytest.raises(ValueError,match='OCR is not configured'):
        list(document(b'bytes','scan.jpg','docs'))


def test_image_upload_preserves_the_original(isolated_document_archive):
    import base64
    from agentai.commands import Upload
    from agentai.ingestion.service import save_upload
    archive=isolated_document_archive
    user={'name':'indexer','roles':['indexer'],'sources':['docs']}
    body=Upload(source='docs',filename='scan.png',content_base64=base64.b64encode(b'original image').decode())
    version=save_upload(body,user,archive=lambda:archive)
    assert archive.path(version).read_bytes()==b'original image'
    assert archive.get(version,['docs'])['filename']=='scan.png'


def test_failed_recognition_never_starts_publication(monkeypatch,isolated_document_archive):
    from agentai.ingestion.service import index_version
    from agentai.errors import ApplicationError
    archive=isolated_document_archive
    writer=PdfWriter(); writer.add_blank_page(width=200,height=200)
    data=BytesIO(); writer.write(data)
    version=archive.save('docs','scan','scan.pdf',data.getvalue(),'indexer')
    monkeypatch.setenv('OCR_URL','http://ocr:8000')
    monkeypatch.setattr(ocr,'recognize',lambda *a: (_ for _ in ()).throw(ValueError('offline')))
    calls=[]
    with pytest.raises(ApplicationError):
        index_version(version,{'roles':['indexer'],'sources':['docs']},
                      archive=lambda:archive,services=lambda:calls.append('publication'))
    assert not calls
    assert archive.path(version).read_bytes()==data.getvalue()

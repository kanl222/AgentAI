import hashlib
import importlib.util
from pathlib import Path
import uuid

spec=importlib.util.spec_from_file_location('upload_folder',Path(__file__).resolve().parents[1]/'scripts'/'upload_folder.py')
folder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(folder)


def test_collect_subfolders_and_exclusions(tmp_path):
    (tmp_path/'one.md').write_text('hello')
    (tmp_path/'ignore.exe').write_bytes(b'no')
    (tmp_path/'nested').mkdir()
    (tmp_path/'nested'/'two.PDF').write_bytes(b'pdf')
    (tmp_path/'node_modules').mkdir()
    (tmp_path/'node_modules'/'secret.txt').write_text('skip')
    (tmp_path/'.hidden').mkdir()
    (tmp_path/'.hidden'/'skip.md').write_text('skip')
    assert {p.relative_to(tmp_path).as_posix() for p in folder.collect(tmp_path)}=={'one.md','nested/two.PDF'}


def test_stable_identity_matches_single_upload():
    assert folder.identity('docs-demo',Path('one.md'))==uuid.uuid5(uuid.NAMESPACE_URL,'docs-demo/one.md').hex
    assert folder.identity('a',Path('nested/one.md'))!=folder.identity('a',Path('one.md'))
    assert folder.identity('a',Path('one.md'),'team-a')!=folder.identity('a',Path('one.md'),'team-b')


def test_upload_only_changed_files(tmp_path,monkeypatch):
    (tmp_path/'one.md').write_text('same')
    (tmp_path/'two.md').write_text('changed')
    known={folder.identity('a',Path('one.md')):hashlib.sha256(b'same').hexdigest()}
    sent=[]
    def request(url,key,body=None):
        sent.append(body)
        return {'chunks':1}
    monkeypatch.setattr(folder,'request',request)
    counts=folder.upload_all(tmp_path,'a',folder.collect(tmp_path),'http://example.test','test-key',known)
    assert counts=={'uploaded':1,'unchanged':1,'failed':0}
    assert sent[0]['filename']=='two.md'


def test_manifest_pagination(monkeypatch):
    calls=[]
    def request(url,key):
        calls.append(url)
        if 'offset=0' in url:
            return {'versions':[{'source':'a','state':'indexed','document_id':str(i),'sha256':'test'} for i in range(200)]}
        return {'versions':[{'source':'b','state':'indexed','document_id':'other','sha256':'other'}]}
    monkeypatch.setattr(folder,'request',request)
    assert len(folder.manifest('http://example.test','test-key','a'))==200
    assert len(calls)==2 and 'offset=200' in calls[-1]

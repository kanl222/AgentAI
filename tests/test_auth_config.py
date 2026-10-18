import hashlib
import json
import pytest
from fastapi import HTTPException
from agentai.core import authenticate


@pytest.mark.parametrize('value',['[invalid]','{}','[{}]',json.dumps([{'sha256':'wrong','roles':['reader'],'sources':['a'],'name':'a'}])])
def test_invalid_user_config_fails_safely(monkeypatch,value):
    monkeypatch.setenv('AGENT_USERS',value)
    with pytest.raises(HTTPException) as error:
        authenticate('Bearer example-key')
    assert error.value.status_code==503
    assert value not in error.value.detail


def test_valid_reader_key(monkeypatch):
    row={'name':'reader','sha256':hashlib.sha256(b'example-key').hexdigest(),'roles':['reader'],'sources':['docs-demo']}
    monkeypatch.setenv('AGENT_USERS',json.dumps([row]))
    assert authenticate('Bearer example-key')['sources']==['docs-demo']

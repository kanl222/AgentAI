"""Unit tests for require as a decorator and direct permission checker."""
import pytest
from fastapi import HTTPException
from agentai.access import require, check_access
from agentai.core import require as core_require
from agentai.errors import ApplicationError


def test_direct_call_success():
    user = {'name': 'alice', 'roles': ['reader', 'indexer'], 'sources': ['src1']}
    assert require(user, 'reader') is None
    assert require(user, 'indexer', 'src1') is None


def test_direct_call_denied():
    user = {'name': 'bob', 'roles': ['reader'], 'sources': ['src1']}
    with pytest.raises(ApplicationError) as exc_info:
        require(user, 'indexer')
    assert exc_info.value.status_code == 403

    with pytest.raises(ApplicationError) as exc_info:
        require(user, 'reader', 'src2')
    assert exc_info.value.status_code == 403


def test_direct_call_no_user():
    with pytest.raises(ApplicationError) as exc_info:
        require(None, 'reader')
    assert exc_info.value.status_code == 401


def test_core_direct_call_translates_to_http_exception():
    user = {'name': 'bob', 'roles': ['reader'], 'sources': []}
    assert core_require(user, 'reader') is None
    with pytest.raises(HTTPException) as exc_info:
        core_require(user, 'sql_reader')
    assert exc_info.value.status_code == 403


def test_decorator_with_p_parameter():
    @require('reader')
    def get_data(p):
        """Docstring test."""
        return f"ok:{p['name']}"

    assert get_data.__name__ == 'get_data'
    assert get_data.__doc__ == 'Docstring test.'

    user_ok = {'name': 'alice', 'roles': ['reader'], 'sources': []}
    user_bad = {'name': 'guest', 'roles': [], 'sources': []}

    assert get_data(user_ok) == 'ok:alice'
    assert get_data(p=user_ok) == 'ok:alice'

    with pytest.raises(ApplicationError) as exc_info:
        get_data(user_bad)
    assert exc_info.value.status_code == 403


def test_decorator_with_user_parameter():
    @require('reader')
    def fetch_profile(user):
        return user['name']

    user_ok = {'name': 'alice', 'roles': ['reader'], 'sources': []}
    user_bad = {'name': 'guest', 'roles': [], 'sources': []}

    assert fetch_profile(user_ok) == 'alice'
    with pytest.raises(ApplicationError) as exc_info:
        fetch_profile(user_bad)
    assert exc_info.value.status_code == 403


def test_decorator_with_self_user_method():
    class Context:
        def __init__(self, user):
            self.user = user

        @require('reader')
        def execute(self, value):
            return f"executed:{value}"

    ctx_ok = Context({'name': 'alice', 'roles': ['reader'], 'sources': []})
    ctx_bad = Context({'name': 'guest', 'roles': [], 'sources': []})

    assert ctx_ok.execute(42) == 'executed:42'
    with pytest.raises(ApplicationError) as exc_info:
        ctx_bad.execute(42)
    assert exc_info.value.status_code == 403


def test_decorator_with_attribute_source():
    class RequestBody:
        def __init__(self, source):
            self.source = source

    @require('indexer', source='body.source')
    def process_body(body, p):
        return body.source

    user = {'name': 'alice', 'roles': ['indexer'], 'sources': ['allowed']}

    assert process_body(RequestBody('allowed'), p=user) == 'allowed'

    with pytest.raises(ApplicationError) as exc_info:
        process_body(RequestBody('forbidden'), p=user)
    assert exc_info.value.status_code == 403


def test_decorator_with_callable_source():
    @require('indexer', source=lambda item, **kw: item['source'])
    def process_item(item, p):
        return item['source']

    user = {'name': 'alice', 'roles': ['indexer'], 'sources': ['allowed']}

    assert process_item({'source': 'allowed'}, p=user) == 'allowed'

    with pytest.raises(ApplicationError) as exc_info:
        process_item({'source': 'forbidden'}, p=user)
    assert exc_info.value.status_code == 403


def test_decorator_bare():
    @require
    def default_reader_action(p):
        return 'done'

    user_ok = {'name': 'alice', 'roles': ['reader'], 'sources': []}
    user_bad = {'name': 'guest', 'roles': [], 'sources': []}

    assert default_reader_action(p=user_ok) == 'done'
    with pytest.raises(ApplicationError) as exc_info:
        default_reader_action(p=user_bad)
    assert exc_info.value.status_code == 403


def test_core_require_decorator():
    @core_require('sql_reader')
    def read_schema(p):
        return 'schema'

    user_ok = {'name': 'alice', 'roles': ['sql_reader'], 'sources': []}
    user_bad = {'name': 'bob', 'roles': ['reader'], 'sources': []}

    assert read_schema(p=user_ok) == 'schema'
    with pytest.raises(HTTPException) as exc_info:
        read_schema(p=user_bad)
    assert exc_info.value.status_code == 403


def _run_coroutine(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value


def test_async_decorator():
    @require('reader')
    async def async_action(p):
        return f"async:{p['name']}"

    user_ok = {'name': 'alice', 'roles': ['reader'], 'sources': []}
    user_bad = {'name': 'guest', 'roles': [], 'sources': []}

    res = _run_coroutine(async_action(p=user_ok))
    assert res == 'async:alice'

    with pytest.raises(ApplicationError) as exc_info:
        _run_coroutine(async_action(p=user_bad))
    assert exc_info.value.status_code == 403


def test_async_core_decorator():
    @core_require('reader')
    async def async_core_action(p):
        return 'core_async'

    user_bad = {'name': 'guest', 'roles': [], 'sources': []}
    with pytest.raises(HTTPException) as exc_info:
        _run_coroutine(async_core_action(p=user_bad))
    assert exc_info.value.status_code == 403


def test_core_bare_decorator_preserves_action_and_denies_access():
    @core_require
    def action(p):
        return p['name']

    assert action(p={'name': 'alice', 'roles': ['reader'], 'sources': []}) == 'alice'
    with pytest.raises(HTTPException) as error:
        action(p={'roles': [], 'sources': []})
    assert error.value.status_code == 403


def test_explicit_missing_principal_cannot_be_replaced_by_payload():
    @require('reader')
    def action(body, p=None):
        return 'executed'

    with pytest.raises(ApplicationError) as error:
        action({'roles': ['reader'], 'sources': ['private']}, p=None)
    assert error.value.status_code == 401


def test_configured_source_cannot_disappear_during_resolution():
    @require('indexer', source='body.source')
    def action(body, p):
        return 'executed'

    with pytest.raises(ApplicationError) as error:
        action({}, p={'roles': ['indexer'], 'sources': []})
    assert error.value.status_code == 403


def test_literal_source_with_dot_still_requires_permission():
    @require('reader', source='repo.private')
    def action(p):
        return 'executed'

    assert action(p={'roles': ['reader'], 'sources': ['repo.private']}) == 'executed'
    with pytest.raises(ApplicationError) as error:
        action(p={'roles': ['reader'], 'sources': []})
    assert error.value.status_code == 403


def test_core_bare_async_decorator():
    @core_require
    async def action(p):
        return p['name']

    assert _run_coroutine(action(p={'name': 'alice', 'roles': ['reader'], 'sources': []})) == 'alice'
    with pytest.raises(HTTPException) as error:
        _run_coroutine(action(p={'roles': [], 'sources': []}))
    assert error.value.status_code == 403

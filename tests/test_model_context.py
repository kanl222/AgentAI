import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import httpx
import pytest

from agentai.adapters.model_context import context_status
from agentai.adapters import service_checks
from agentai.settings import ModelSettings

script = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/configure_model_context.py'))


@pytest.mark.parametrize(('value', 'tokens'), [('32768', 16384), ('8192', 2048)])
def test_context_and_output_have_separate_limits(monkeypatch, value, tokens):
    monkeypatch.setenv('CHAT_CONTEXT_LENGTH', value)
    monkeypatch.setenv('CHAT_MAX_TOKENS', str(tokens))
    settings = ModelSettings.from_env()
    assert settings.context_length == int(value) and settings.max_tokens == tokens


@pytest.mark.parametrize('value', ['-1', '0', '511', '16384', 'abc'])
def test_invalid_context_rejected(monkeypatch, value):
    monkeypatch.setenv('CHAT_CONTEXT_LENGTH', value)
    monkeypatch.setenv('CHAT_MAX_TOKENS', '16384')
    with pytest.raises(ValueError):
        ModelSettings.from_env()


@pytest.mark.parametrize(('instances', 'state', 'lengths'), [
    ([], 'not_loaded', []),
    ([{'id': 'alias', 'config': {'context_length': 32768}}], 'matched', [32768]),
    ([{'id': 'alias', 'config': {'context_length': 8192}}], 'mismatch', [8192]),
])
def test_diagnostics_read_actual_context(instances, state, lengths):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'models': [{'key': 'qwen', 'loaded_instances': instances}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert context_status(client, 'http://lm/prefix/v1', 'key', 'alias', 32768) == {
            'configured': 32768, 'loaded': lengths, 'status': state}
    assert str(seen[0].url) == 'http://lm/prefix/api/v1/models'
    assert seen[0].headers['authorization'] == 'Bearer key'


def test_config_file_only_reads_settings_and_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setenv('CHAT_CONTEXT_LENGTH', '65536')
    path = tmp_path / '.env'
    path.write_text('CHAT_CONTEXT_LENGTH=32768\nCHAT_MODEL="qwen" # comment\nDATABASE_URL=private\n'
                    'CHAT_MAX_TOKENS=2048 # comment\n', encoding='utf-8')
    assert script['read_config'](path) == {
        'CHAT_CONTEXT_LENGTH': '65536', 'CHAT_MODEL': 'qwen', 'CHAT_MAX_TOKENS': '2048'}


def cli_runner(initial):
    commands = []
    instances = list(initial)
    def run(args, **kwargs):
        commands.append(args[1:])
        assert kwargs['check'] and kwargs['timeout'] == 240
        if args[1] == 'ps':
            return SimpleNamespace(stdout=json.dumps(instances))
        if args[1] == 'unload':
            instances.clear()
        if args[1] == 'load' and '--estimate-only' not in args:
            instances.append({'identifier': 'qwen', 'modelKey': 'qwen', 'contextLength': int(args[args.index('--context-length')+1])})
        return SimpleNamespace(stdout='')
    return commands, run


def test_apply_uses_context_configuration_and_is_idempotent():
    settings = ModelSettings(base_url='http://lm/v1', api_key='test', chat_model='qwen', context_length=32768)
    commands, run = cli_runner([])
    assert script['apply_context'](settings, 'lms', run=run)['changed'] is True
    assert ['load', 'qwen', '--context-length', '32768', '--parallel', '1', '--identifier', 'qwen'] in commands
    commands.clear()
    assert script['apply_context'](settings, 'lms', run=run)['changed'] is False
    assert commands == [['ps', '--json']]


@pytest.mark.parametrize(('status', 'queued', 'reload', 'allowed'), [
    ('idle', 0, False, False), ('generating', 0, True, False),
    ('idle', 1, True, False), ('idle', 0, True, True),
])
def test_replace_requires_explicit_reload_and_idle_model(status, queued, reload, allowed):
    settings = ModelSettings(base_url='http://lm/v1', api_key='test', chat_model='qwen', context_length=32768)
    commands, run = cli_runner([{'identifier': 'qwen', 'modelKey': 'qwen', 'contextLength': 8192,
                                'status': status, 'queued': queued}])
    if allowed:
        assert script['apply_context'](settings, 'lms', reload, run)['changed']
        assert ['unload', 'qwen'] in commands
    else:
        with pytest.raises(ValueError):
            script['apply_context'](settings, 'lms', reload, run)
        assert commands == [['ps', '--json']]


def test_unrelated_model_instance_is_preserved():
    settings = ModelSettings(base_url='http://lm/v1', api_key='test', chat_model='qwen', context_length=32768)
    commands, run = cli_runner([{'identifier': 'other', 'modelKey': 'qwen', 'contextLength': 8192, 'status': 'idle'}])
    with pytest.raises(ValueError, match='another identifier'):
        script['apply_context'](settings, 'lms', True, run)
    assert commands == [['ps', '--json']]

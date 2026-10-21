"""Apply CHAT_CONTEXT_LENGTH from project configuration on the LM Studio host."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from agentai.settings import ModelSettings

KEYS = {'CHAT_MODEL', 'CHAT_CONTEXT_LENGTH', 'CHAT_MAX_TOKENS'}


def read_config(path):
    """Read only the three non-secret scalar settings; process environment wins."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.strip().removeprefix('export ').partition('=')
            key = key.strip()
            if separator and key in KEYS:
                value = value.strip()
                if value.startswith(('"', "'")):
                    quote = value[0]
                    end = value.find(quote, 1)
                    if end < 0 or value[end+1:].strip() and not value[end+1:].strip().startswith('#'):
                        raise ValueError(f'Invalid {key} in configuration')
                    value = value[1:end]
                else:
                    value = value.split(' #', 1)[0].strip()
                values[key] = value
    return {key: os.environ.get(key, value) for key, value in values.items()}


def apply_context(settings, lms, reload=False, run=subprocess.run):
    if settings.context_length is None:
        raise ValueError('Set CHAT_CONTEXT_LENGTH in .env')

    def command(*args):
        return run([str(lms), *args], check=True, capture_output=True, text=True, encoding='utf-8', timeout=240)

    def loaded():
        return [model for model in json.loads(command('ps', '--json').stdout)
                if model.get('identifier') == settings.chat_model or model.get('modelKey') == settings.chat_model]

    instances = loaded()
    if any(model.get('identifier') != settings.chat_model for model in instances):
        raise ValueError('Model is loaded under another identifier; unload it in LM Studio before applying configuration')
    if instances and all(model.get('contextLength') == settings.context_length for model in instances):
        return {'model': settings.chat_model, 'context_length': settings.context_length, 'changed': False}
    if instances and not reload:
        raise ValueError('Context differs. Finish active requests and run this command with --reload')
    if any(model.get('status') != 'idle' or model.get('queued', 0) for model in instances):
        raise ValueError('Model is busy. Finish active requests before changing context')
    # Ask LM Studio to estimate the requested load before changing the instance.
    command('load', settings.chat_model, '--context-length', str(settings.context_length), '--parallel', '1', '--estimate-only')
    for model in instances:
        command('unload', model['identifier'])
    command('load', settings.chat_model, '--context-length', str(settings.context_length),
            '--parallel', '1', '--identifier', settings.chat_model)
    actual = loaded()
    if not actual or any(model.get('contextLength') != settings.context_length for model in actual):
        raise ValueError('LM Studio did not confirm CHAT_CONTEXT_LENGTH; check lms ps --json')
    return {'model': settings.chat_model, 'context_length': settings.context_length, 'changed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--reload', action='store_true', help='Replace an idle instance with a different context')
    parser.add_argument('--lms', type=Path, help='Path to the LM Studio CLI')
    args = parser.parse_args()
    try:
        os.environ.update(read_config(args.env_file))
        settings = ModelSettings.from_env()
        lms = args.lms or shutil.which('lms') or Path.home() / '.lmstudio/bin/lms.exe'
        if not Path(lms).is_file():
            raise ValueError('LM Studio CLI not found; provide --lms PATH')
        print(json.dumps(apply_context(settings, lms, args.reload), ensure_ascii=False))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        # CLI stderr may contain local paths or configuration; keep it out of API/log sharing.
        print('Не удалось применить контекст. Проверьте CHAT_CONTEXT_LENGTH, CHAT_MAX_TOKENS, '
              'запуск LM Studio и доступную память. При другом загруженном контексте '
              'завершите запросы и используйте --reload. Подробности доступны в LM Studio.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

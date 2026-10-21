"""Read-only dependency probes; never expose URLs, credentials or response bodies."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from time import monotonic

import httpx
import psycopg
from ..settings import ModelSettings
from .model_context import context_status


def _error(code, message, hint, **extra):
    return {'status': 'error', 'code': code, 'message': message, 'hint': hint, **extra}


def _probe(check):
    start = monotonic()
    try:
        result = check()
    except httpx.TimeoutException:
        result = _error('timeout', 'Сервис не ответил вовремя.', 'Проверьте нагрузку и доступность сервиса.')
    except httpx.ConnectError:
        result = _error('connection_failed', 'Не удалось подключиться к сервису.',
                        'Проверьте запуск сервиса, адрес, порт и доступ из контейнера API.')
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        hint = ('Проверьте ключ доступа к сервису.' if status in {401, 403}
                else 'Проверьте состояние сервиса и его журналы; проверьте адрес API.')
        result = _error('http_error', f'Сервис вернул HTTP {status}.', hint, http_status=status)
    except psycopg.Error as error:
        code = error.sqlstate or ''
        if isinstance(error, (psycopg.errors.ConnectionTimeout, psycopg.errors.QueryCanceled)):
            result = _error('timeout', 'PostgreSQL не ответил вовремя.',
                            'Проверьте доступность базы, нагрузку и её журналы.')
        elif code.startswith('28'):
            result = _error('authentication_failed', 'PostgreSQL отклонил авторизацию.',
                            'Проверьте пользователя и пароль в DATABASE_URL.')
        elif code == '3D000':
            result = _error('database_missing', 'База PostgreSQL не найдена.',
                            'Проверьте имя базы в DATABASE_URL и создание базы.')
        else:
            result = _error('database_unavailable', 'Не удалось выполнить проверку PostgreSQL.',
                            'Проверьте DATABASE_URL, доступность базы, права и её журналы.')
    except (ValueError, KeyError, TypeError):
        result = _error('invalid_response', 'Некорректная конфигурация или ответ сервиса.',
                        'Проверьте адрес API и совместимость версии сервиса.')
    except Exception:
        result = _error('check_failed', 'Проверка сервиса завершилась ошибкой.',
                        'Проверьте конфигурацию и журналы сервиса.')
    return {**result, 'latency_ms': round((monotonic() - start) * 1000)}


def _postgres():
    dsn = os.getenv('DATABASE_URL', '').strip()
    if not dsn:
        return _error('not_configured', 'PostgreSQL не настроен.', 'Укажите DATABASE_URL.')
    # Do not call bootstrap.database(): readiness must not run migrations.
    with psycopg.connect(dsn, connect_timeout=5, options='-c statement_timeout=5000') as db:
        db.execute('SELECT 1').fetchone()
    return {'status': 'ok', 'message': 'Подключение к PostgreSQL работает.'}


def _http(name):
    config = {
        'lmstudio': ('LMSTUDIO_BASE_URL', 'http://host.docker.internal:1234/v1', '/models'),
        'qdrant': ('QDRANT_URL', 'http://qdrant:6333', '/readyz'),
        'ocr': ('OCR_URL', '', '/health'),
    }
    variable, default, path = config[name]
    url = os.getenv(variable, default).strip().rstrip('/')
    if not url:
        if name == 'ocr':
            return {'status': 'disabled', 'message': 'OCR отключён: OCR_URL не задан.'}
        return _error('not_configured', 'Адрес сервиса не задан.', f'Укажите {variable}.')
    headers = ({'Authorization': 'Bearer ' + os.getenv('LMSTUDIO_API_KEY', 'lm-studio')}
               if name == 'lmstudio' else {})
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(url + path, headers=headers)
        response.raise_for_status()
    if name == 'lmstudio':
        names = {model['id'] for model in response.json()['data']}
        roles = [('chat', 'CHAT_MODEL', 'qwen3-4b')]
        if not os.getenv('EMBED_BASE_URL', '').strip():
            roles.append(('embeddings', 'EMBED_MODEL', 'text-embedding-nomic-embed-text-v1.5'))
        models = {role: {'id': os.getenv(variable, default),
                         'available': os.getenv(variable, default) in names}
                  for role, variable, default in roles}
        if not all(model['available'] for model in models.values()):
            return _error('models_missing', 'LM Studio доступен, но настроенные модели не найдены.',
                          'Проверьте CHAT_MODEL и EMBED_MODEL, доступность моделей в LM Studio.', models=models)
        result = {'status': 'ok', 'message': 'LM Studio доступен, настроенные модели есть в списке.', 'models': models}
        try:
            settings = ModelSettings.from_env()
        except ValueError:
            return _error('invalid_configuration', 'Некорректные настройки модели.',
                          'Проверьте параметры CHAT_*: CHAT_CONTEXT_LENGTH не меньше 512, '
                          'CHAT_MAX_TOKENS должен быть меньше размера контекста.', models=models)
        if settings.context_length is not None:
            with httpx.Client(timeout=5, trust_env=False) as client:
                context = context_status(client, url, settings.api_key, settings.chat_model, settings.context_length)
            result['context'] = context
            if context['status'] != 'matched':
                return _error('context_not_applied', 'Настроенный контекст модели не применён.',
                              'На компьютере с LM Studio выполните python scripts/configure_model_context.py; '
                              'для замены загруженного экземпляра используйте --reload после завершения запросов.',
                              models=models, context=context)
        return result
    return {'status': 'ok', 'message': 'Сервис отвечает на проверку доступности.'}


def _embeddings():
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.get(os.environ['EMBED_BASE_URL'].strip().rstrip('/')+'/models',
                              headers={'Authorization': 'Bearer '+os.getenv('EMBED_API_KEY', 'local-embeddings')})
        response.raise_for_status()
    wanted = os.getenv('EMBED_MODEL', 'text-embedding-nomic-embed-text-v1.5')
    names = {row['id'] for row in response.json()['data']}
    if wanted not in names:
        return _error('models_missing', 'Настроенная embedding-модель не найдена.',
                      'Проверьте EMBED_BASE_URL, EMBED_MODEL и запуск сервиса embeddings.')
    return {'status': 'ok', 'message': 'Embedding-сервис доступен, модель загружена.', 'model': wanted}


def check_services():
    checks = {'postgres': _postgres, **{name: lambda name=name: _http(name)
                                      for name in ('qdrant', 'lmstudio', 'ocr')}}
    if os.getenv('EMBED_BASE_URL', '').strip():
        checks['embeddings'] = _embeddings
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        services = dict(zip(checks, pool.map(_probe, checks.values())))
    failed = [name for name, result in services.items() if result['status'] == 'error']
    return {'status': 'unavailable' if failed else 'ready',
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'failed_services': failed, 'services': services,
            'scope': 'Проверяется доступность сервисов и список моделей; генерация, эмбеддинги и распознавание не запускаются.'}

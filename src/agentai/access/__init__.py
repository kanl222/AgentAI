"""Authentication and authorization, independent of HTTP transport."""
import functools
import hashlib
import hmac
import inspect
import json
import os
import re
from ..errors import ApplicationError


def authenticate(header):
    """Аутентифицирует пользователя по Bearer-токену из заголовка Authorization.

    Args:
        header: Значение заголовка авторизации (например, 'Bearer <token>').

    Returns:
        Словарь данных пользователя из конфигурации AGENT_USERS.

    Raises:
        ApplicationError: Если заголовок отсутствует (401), ключ недействителен (401)
            или конфигурация повреждена (503).
    """
    if not header or not header.startswith('Bearer '):
        raise ApplicationError(401, 'Bearer key required')
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    try:
        users = json.loads(os.getenv('AGENT_USERS', '[]'))
        if not isinstance(users, list):
            raise ValueError('Expected users array')
        for entry in users:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get('name'), str)
                    or not isinstance(entry.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                    or not all(isinstance(entry.get(field), list)
                               and all(isinstance(value, str) for value in entry[field])
                               for field in ('roles', 'sources'))):
                raise ValueError('Invalid user record')
    except (ValueError, TypeError):
        raise ApplicationError(503, 'Invalid AGENT_USERS configuration; contact the administrator') from None
    for user in users:
        if hmac.compare_digest(digest, user['sha256']):
            return user
    raise ApplicationError(401, 'Invalid key')


def check_access(user, role, source=None):
    """Проверяет соответствие роли и источника данных правам пользователя.

    Args:
        user: Словарь или объект с данными пользователя (roles, sources).
        role: Требуемая роль ('reader', 'indexer', 'sql_reader' и т.д.).
        source: Требуемый источник данных (опционально).

    Returns:
        None

    Raises:
        ApplicationError: Если контекст пользователя не передан (401) или доступ запрещен (403).
    """
    if not user:
        raise ApplicationError(401, 'User context required')
    roles = user.get('roles') if isinstance(user, dict) else getattr(user, 'roles', None)
    sources = user.get('sources') if isinstance(user, dict) else getattr(user, 'sources', None)
    if roles is None or role not in roles:
        raise ApplicationError(403, 'Access denied')
    if source is not None and (sources is None or source not in sources):
        raise ApplicationError(403, 'Access denied')


def _extract_user(call_args, args):
    """Извлекает объект пользователя из переданных аргументов функции.

    Args:
        call_args: Словарь привязанных аргументов вызова функции.
        args: Список позиционных аргументов вызова.

    Returns:
        Объект или словарь пользователя, либо None если пользователь не найден.
    """
    if 'user' in call_args:
        return call_args['user']
    if 'p' in call_args:
        return call_args['p']
    if 'principal' in call_args:
        return call_args['principal']
    if 'self' in call_args:
        self_obj = call_args['self']
        for name in ('user', 'p', 'principal'):
            if hasattr(self_obj, name):
                return getattr(self_obj, name)
    for val in call_args.values():
        if isinstance(val, dict) and 'roles' in val:
            return val
    for val in args:
        if isinstance(val, dict) and 'roles' in val:
            return val
    return None


def _extract_source(source, call_args, args, kwargs):
    """Вычисляет требуемый источник данных из конфигурации и аргументов вызова.

    Args:
        source: Конфигурация источника (строка, callable или None).
        call_args: Словарь привязанных аргументов вызова.
        args: Позиционные аргументы вызова.
        kwargs: Именованные аргументы вызова.

    Returns:
        Имя источника данных в виде строки или None.
    """
    if source is None:
        return None
    if callable(source):
        try:
            return source(**call_args)
        except TypeError:
            return source(*args, **kwargs)
    if isinstance(source, str):
        if '.' in source and source.split('.')[0] in call_args:
            parts = source.split('.')
            val = call_args.get(parts[0])
            for part in parts[1:]:
                if val is None:
                    break
                val = getattr(val, part, None) if not isinstance(val, dict) else val.get(part)
            return val
        if source in call_args:
            val = call_args[source]
            if isinstance(val, str):
                return val
            if hasattr(val, 'source'):
                return getattr(val, 'source')
            if isinstance(val, dict) and 'source' in val:
                return val['source']
            return val
        return source
    return source


def _is_direct_call(args, kwargs):
    """Определяет, вызван ли require напрямую как функция проверки, а не декоратор.

    Args:
        args: Позиционные аргументы.
        kwargs: Именованные аргументы.

    Returns:
        True, если вызов является прямой проверкой (например require(user, 'reader')).
    """
    if 'user' in kwargs:
        return True
    if not args:
        return False
    first = args[0]
    if isinstance(first, dict) or (not isinstance(first, (str, bytes)) and not callable(first)):
        return True
    return False


def require(*args, **kwargs):
    """Декоратор и функция проверки прав доступа.

    Может использоваться как декоратор функции (@require('reader'), @require('indexer', source=...)),
    а также для прямой проверки прав (require(user, 'reader', source)).

    Args:
        *args: При прямой проверке: (user, role, [source]).
               При использовании как декоратор: (role, [source]) или декорируемая функция.
        **kwargs: Именованные аргументы (user, role, source).

    Returns:
        Декоратор функции при использовании в качестве декоратора или None при прямой проверке.

    Raises:
        ApplicationError: Если контекст пользователя не найден (401) или доступ запрещен (403).
    """
    if _is_direct_call(args, kwargs):
        user = args[0] if len(args) > 0 else kwargs.get('user')
        role = args[1] if len(args) > 1 else kwargs.get('role')
        source = args[2] if len(args) > 2 else kwargs.get('source')
        return check_access(user, role, source)

    if len(args) == 1 and callable(args[0]) and not isinstance(args[0], (str, bytes)):
        # Применение без параметров: @require
        fn = args[0]
        return require('reader')(fn)

    role = args[0] if len(args) > 0 else kwargs.get('role', 'reader')
    source = args[1] if len(args) > 1 else kwargs.get('source')

    def decorator(fn):
        sig = inspect.signature(fn)

        def authorize(f_args, f_kwargs):
            bound = sig.bind(*f_args, **f_kwargs)
            bound.apply_defaults()
            call_args = bound.arguments
            u = _extract_user(call_args, f_args)
            s = _extract_source(source, call_args, f_args, f_kwargs)
            if source is not None and s is None:
                raise ApplicationError(403, 'Access denied')
            check_access(u, role, s)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*f_args, **f_kwargs):
                authorize(f_args, f_kwargs)
                return await fn(*f_args, **f_kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*f_args, **f_kwargs):
                authorize(f_args, f_kwargs)
                return fn(*f_args, **f_kwargs)
            return sync_wrapper

    return decorator

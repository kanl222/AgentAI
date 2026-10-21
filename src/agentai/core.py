"""Compatibility imports; new code uses the owning modules directly."""
import functools
import inspect
import httpx  # Historical test/integration hook; also used by the GitLab adapter.
from fastapi import HTTPException
from .access import authenticate as _authenticate, require as _require
from .errors import ApplicationError
from .ingestion.extract import chunks, document
from .adapters.gitlab import gitlab
from .adapters.sqlserver import sql_metadata
from .knowledge.text import tokenize, lexical_tokens
from .knowledge.store import Store


def authenticate(header):
    """Аутентифицирует пользователя и транслирует ApplicationError в HTTPException.

    Args:
        header: Значение заголовка авторизации Authorization.

    Returns:
        Словарь данных пользователя.

    Raises:
        HTTPException: Если токен не передан (401), неверен (401) или ошибка конфигурации (503).
    """
    try:
        return _authenticate(header)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None


def require(*args, **kwargs):
    """Декоратор и функция проверки прав с трансляцией ошибок в HTTPException.

    Args:
        *args: При прямой проверке: (user, role, [source]).
               При использовании как декоратор: (role, [source]) или декорируемая функция.
        **kwargs: Именованные аргументы (user, role, source).

    Returns:
        Декоратор функции при использовании в качестве декоратора или None при прямой проверке.

    Raises:
        HTTPException: Если контекст пользователя не найден (401) или доступ запрещен (403).
    """
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return require('reader')(args[0])
    try:
        res = _require(*args, **kwargs)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None

    if callable(res):
        def decorator(fn):
            wrapped = res(fn)
            if inspect.iscoroutinefunction(wrapped):
                @functools.wraps(wrapped)
                async def async_wrapper(*a, **kw):
                    try:
                        return await wrapped(*a, **kw)
                    except ApplicationError as error:
                        raise HTTPException(error.status_code, error.detail) from None
                return async_wrapper
            else:
                @functools.wraps(wrapped)
                def sync_wrapper(*a, **kw):
                    try:
                        return wrapped(*a, **kw)
                    except ApplicationError as error:
                        raise HTTPException(error.status_code, error.detail) from None
                return sync_wrapper
        return decorator
    return res

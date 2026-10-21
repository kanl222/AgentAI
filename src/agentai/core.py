"""Compatibility imports; new code uses the owning modules directly."""
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
    try:
        return _authenticate(header)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None


def require(user, role, source=None):
    try:
        return _require(user, role, source)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None

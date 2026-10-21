"""Compatibility entry point for the answer engine."""
from fastapi import HTTPException
from .answering.engine import answer as _answer, skills, State
from .errors import ApplicationError


def answer(question, user, store, model, max_steps=4, history=None):
    try:
        return _answer(question, user, store, model, max_steps, history)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None

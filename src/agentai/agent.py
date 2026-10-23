"""Compatibility entry point for the answer engine."""
from fastapi import HTTPException
from .answering.engine import answer as _answer, answer_with_template as _answer_with_template, skills, State
from .answering.prompt_template import format_rag_prompt
from .errors import ApplicationError


def answer(question, user, store, model, max_steps=4, history=None, use_template=False, instructions=None, **kwargs):
    try:
        return _answer(question, user, store, model, max_steps=max_steps, history=history,
                       use_template=use_template, instructions=instructions, **kwargs)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None


def answer_with_template(question, user, store, model, instructions=None, history=None):
    try:
        return _answer_with_template(question, user, store, model, instructions=instructions, history=history)
    except ApplicationError as error:
        raise HTTPException(error.status_code, error.detail) from None

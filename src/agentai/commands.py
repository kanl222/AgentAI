"""Validated input contracts shared by HTTP and job execution."""
import uuid
from pydantic import BaseModel, Field

class Chat(BaseModel):
    question: str = Field(min_length=1,max_length=12000)
    conversation_id: uuid.UUID | None = None


class Upload(BaseModel):
    source: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    filename: str = Field(max_length=200)
    content_base64: str = Field(max_length=28_000_000)
    document_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')


class Sync(BaseModel):
    source: str


class Completion(BaseModel):
    model: str = 'local-agent'
    messages: list[dict] = Field(max_length=40)
    stream: bool = False
    conversation_id: uuid.UUID | None = None
    chat_id: str | None = Field(default=None,max_length=200)


class DocumentMetadata(BaseModel):
    validity: str = Field(pattern='^(current|obsolete|draft)$')
    note: str = Field(default='',max_length=1000)

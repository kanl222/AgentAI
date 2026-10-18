"""Bounded, authenticated API support for the configured embedding model."""
import base64
import os
import struct
from typing import Annotated, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


Text = Annotated[str, Field(strict=True, min_length=1, max_length=8192)]
Batch = Annotated[list[Text], Field(min_length=1, max_length=16)]


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Text | Batch
    model: str | None = Field(default=None, min_length=1, max_length=200)
    encoding_format: Literal['float', 'base64'] = 'float'

    @model_validator(mode='after')
    def check_input(self):
        texts = [self.input] if isinstance(self.input, str) else self.input
        if any(not text.strip() for text in texts):
            raise ValueError('Input texts must not be blank')
        if sum(len(text) for text in texts) > 32768:
            raise ValueError('Maximum total input length: 32768 characters')
        return self


class EmbeddingItem(BaseModel):
    index: int = Field(strict=True, ge=0)
    embedding: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(min_length=1)


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = Field(strict=True, ge=0)
    total_tokens: int = Field(strict=True, ge=0)


class UpstreamEmbeddings(BaseModel):
    data: list[EmbeddingItem]
    usage: EmbeddingUsage


def request_embeddings(body: EmbeddingRequest, client=None):
    """Call LM Studio without depending on Qdrant or the chat model."""
    model = os.getenv('EMBED_MODEL', 'text-embedding-nomic-embed-text-v1.5')
    if body.model is not None and body.model != model:
        raise HTTPException(400, 'Only the configured EMBED_MODEL is available')
    texts = [body.input] if isinstance(body.input, str) else body.input
    owned = client is None
    try:
        if owned:
            client = httpx.Client(timeout=180, follow_redirects=False)
        response = client.post(
            os.getenv('LMSTUDIO_BASE_URL', 'http://host.docker.internal:1234/v1').rstrip('/') + '/embeddings',
            headers={'Authorization': 'Bearer ' + os.getenv('LMSTUDIO_API_KEY', 'lm-studio')},
            json={'model': model, 'input': texts, 'encoding_format': 'float'},
        )
        if response.status_code in (400, 413, 422):
            raise HTTPException(422, 'LM Studio rejected the input; check text length and model context')
        response.raise_for_status()
        result = UpstreamEmbeddings.model_validate(response.json())
        rows = sorted(result.data, key=lambda row: row.index)
        if [row.index for row in rows] != list(range(len(texts))):
            raise ValueError('Invalid embedding indices')
        if len({len(row.embedding) for row in rows}) != 1:
            raise ValueError('Inconsistent embedding dimensions')
        data = []
        for row in rows:
            vector = row.embedding
            if body.encoding_format == 'base64':
                vector = base64.b64encode(struct.pack('<' + 'f' * len(vector), *vector)).decode('ascii')
            data.append({'object': 'embedding', 'index': row.index, 'embedding': vector})
        return {'object': 'list', 'model': model, 'data': data, 'usage': result.usage.model_dump()}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValidationError, ValueError, OverflowError, struct.error):
        # Do not return upstream errors: they can contain private input or credentials.
        raise HTTPException(503, 'Embeddings unavailable; check LM Studio and EMBED_MODEL') from None
    finally:
        if owned and client is not None:
            client.close()

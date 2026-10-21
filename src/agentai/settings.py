"""Validated configuration snapshots, without reading secrets at import time."""
import os
import math
from dataclasses import dataclass, field


def bounded_int(name, default, minimum, maximum):
    return max(minimum, min(maximum, int(os.getenv(name, str(default)))))


def bounded_float(name, default, minimum, maximum):
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RetrievalSettings:
    candidates: int = 20
    top_k: int = 6
    context_chars: int = 12000
    corpus_entries: int = 4
    corpus_ttl: int = 30
    embedding_entries: int = 64
    embedding_ttl: int = 300

    @classmethod
    def from_env(cls):
        return cls(
            candidates=bounded_int('RAG_CANDIDATES', 20, 6, 100),
            top_k=bounded_int('RAG_TOP_K', 6, 1, 12),
            context_chars=bounded_int('RAG_CONTEXT_CHARS', 12000, 2000, 16000),
            corpus_entries=bounded_int('RETRIEVAL_CACHE_ENTRIES', 4, 0, 16),
            corpus_ttl=bounded_int('RETRIEVAL_CACHE_TTL_SECONDS', 30, 0, 300),
            embedding_entries=bounded_int('QUERY_EMBED_CACHE_ENTRIES', 64, 0, 512),
            embedding_ttl=bounded_int('QUERY_EMBED_CACHE_TTL_SECONDS', 300, 0, 3600),
        )


@dataclass(frozen=True)
class ChunkSettings:
    chars: int = 3500
    overlap: int = 400
    lines: int = 50

    @classmethod
    def from_env(cls):
        chars = bounded_int('RAG_CHUNK_CHARS', 3500, 500, 6000)
        return cls(chars=chars,
                   overlap=min(bounded_int('RAG_CHUNK_OVERLAP', 400, 0, 1000), chars // 4),
                   lines=bounded_int('RAG_CHUNK_LINES', 50, 10, 100))


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str = field(repr=False)
    chat_model: str = 'qwen3-4b'
    embedding_model: str = 'text-embedding-nomic-embed-text-v1.5'
    max_tokens: int = 16384
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 0.0
    context_length: int | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str = field(default='local-embeddings', repr=False)
    embedding_query_instruction: str = ''

    def __post_init__(self):
        if self.context_length is not None:
            if self.context_length < 512:
                raise ValueError('CHAT_CONTEXT_LENGTH must be at least 512')
            if self.max_tokens >= self.context_length:
                raise ValueError('CHAT_MAX_TOKENS must be less than CHAT_CONTEXT_LENGTH to leave room for input')

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.getenv('LMSTUDIO_BASE_URL', 'http://host.docker.internal:1234/v1').rstrip('/'),
            api_key=os.getenv('LMSTUDIO_API_KEY', 'lm-studio'),
            chat_model=os.getenv('CHAT_MODEL', 'qwen3-4b'),
            embedding_model=os.getenv('EMBED_MODEL', 'text-embedding-nomic-embed-text-v1.5'),
            max_tokens=bounded_int('CHAT_MAX_TOKENS', 16384, 256, 32768),
            temperature=bounded_float('CHAT_TEMPERATURE', 0.7, 0, 2),
            top_p=bounded_float('CHAT_TOP_P', 0.8, 0, 1),
            top_k=bounded_int('CHAT_TOP_K', 20, 0, 1000),
            min_p=bounded_float('CHAT_MIN_P', 0.0, 0, 1),
            presence_penalty=bounded_float('CHAT_PRESENCE_PENALTY', 0.0, -2, 2),
            context_length=int(os.environ['CHAT_CONTEXT_LENGTH']) if os.getenv('CHAT_CONTEXT_LENGTH', '').strip() else None,
            embedding_base_url=os.getenv('EMBED_BASE_URL', '').strip().rstrip('/') or None,
            embedding_api_key=os.getenv('EMBED_API_KEY', 'local-embeddings'),
            embedding_query_instruction=os.getenv('EMBED_QUERY_INSTRUCTION', '').strip(),
        )

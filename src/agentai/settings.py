"""Validated configuration snapshots, without reading secrets at import time."""
import os
from dataclasses import dataclass, field


def bounded_int(name, default, minimum, maximum):
    return max(minimum, min(maximum, int(os.getenv(name, str(default)))))


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
    max_tokens: int = 2400

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.getenv('LMSTUDIO_BASE_URL', 'http://host.docker.internal:1234/v1').rstrip('/'),
            api_key=os.getenv('LMSTUDIO_API_KEY', 'lm-studio'),
            chat_model=os.getenv('CHAT_MODEL', 'qwen3-4b'),
            embedding_model=os.getenv('EMBED_MODEL', 'text-embedding-nomic-embed-text-v1.5'),
            max_tokens=bounded_int('CHAT_MAX_TOKENS', 2400, 256, 4096),
        )

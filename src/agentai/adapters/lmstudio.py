"""Construct the configured chat and embedding model adapters."""
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ..settings import ModelSettings


def lmstudio_clients(http_client=None):
    settings = ModelSettings.from_env()
    options = dict(base_url=settings.base_url, api_key=settings.api_key,
                   timeout=180, max_retries=0)
    if http_client is not None:
        options['http_client'] = http_client
    embeddings = OpenAIEmbeddings(model=settings.embedding_model,
                                  check_embedding_ctx_length=False, chunk_size=16, **options)
    model = ChatOpenAI(model=settings.chat_model, temperature=0,
                       max_tokens=settings.max_tokens, **options)
    return embeddings, model

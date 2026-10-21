"""Construct the configured chat and embedding model adapters."""
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ..settings import ModelSettings
from .embedding_profile import InstructionEmbeddings


def lmstudio_clients(http_client=None):
    settings = ModelSettings.from_env()
    options = dict(base_url=settings.base_url, api_key=settings.api_key,
                   timeout=180, max_retries=0)
    if http_client is not None:
        options['http_client'] = http_client
    embedding_options = {**options}
    if settings.embedding_base_url:
        embedding_options.update(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key)
    embeddings = OpenAIEmbeddings(model=settings.embedding_model,
                                  check_embedding_ctx_length=False, chunk_size=16, **embedding_options)
    if settings.embedding_query_instruction:
        embeddings = InstructionEmbeddings(embeddings, settings.embedding_query_instruction)
    model = ChatOpenAI(model=settings.chat_model, temperature=settings.temperature,
                       top_p=settings.top_p, presence_penalty=settings.presence_penalty,
                       extra_body={'top_k': settings.top_k, 'min_p': settings.min_p},
                       max_tokens=settings.max_tokens, **options)
    return embeddings, model

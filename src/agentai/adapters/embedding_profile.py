"""Keep retrieval instructions on queries and original text on documents."""
from langchain_core.embeddings import Embeddings


class InstructionEmbeddings(Embeddings):
    def __init__(self, backend, instruction):
        self.backend = backend
        self.instruction = instruction

    def embed_documents(self, texts):
        return self.backend.embed_documents(texts)

    def embed_query(self, text):
        return self.backend.embed_query(f'Instruct: {self.instruction}\nQuery:{text}')

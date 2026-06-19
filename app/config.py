import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    understanding_model: str = os.getenv("UNDERSTANDING_MODEL", "gpt-4o-mini")
    embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    data_dir: Path = BASE_DIR / os.getenv("DATA_DIR", "data")
    vectorstore_dir: Path = BASE_DIR / os.getenv("VECTORSTORE_DIR", "vectorstore")
    retriever_top_k: int = int(os.getenv("RETRIEVER_TOP_K", "4"))

    chroma_persist_dir: Path = BASE_DIR / os.getenv("CHROMA_PERSIST_DIR", "chroma_store")
    chroma_collection_name: str = os.getenv("CHROMA_COLLECTION_NAME", "manascience_knowledge")
    knowledge_embedding_model: str = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "text-embedding-3-small")
    knowledge_top_k: int = int(os.getenv("KNOWLEDGE_TOP_K", "8"))
    knowledge_max_returned_chunks: int = int(os.getenv("KNOWLEDGE_MAX_RETURNED_CHUNKS", "4"))
    knowledge_max_context_chars: int = int(os.getenv("KNOWLEDGE_MAX_CONTEXT_CHARS", "6000"))
    rag_similarity_threshold: float = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.35"))
    rag_min_relevant_chunks: int = int(os.getenv("RAG_MIN_RELEVANT_CHUNKS", "1"))

    def validate(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
            )


settings = Settings()

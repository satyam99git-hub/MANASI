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

    def validate(self) -> None:
        if not self.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
            )


settings = Settings()

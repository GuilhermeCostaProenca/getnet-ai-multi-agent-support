"""Application settings, loaded from environment variables / .env (NFR-CONF-01)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    chat_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"
    moderation_model: str = "omni-moderation-latest"

    chroma_path: str = "./data/chroma"
    chroma_collection: str = "getnet_kb"
    auto_ingest: bool = True
    rag_top_k: int = 8
    rag_min_similarity: float = 0.30

    router_min_confidence: float = 0.55
    moderation_enabled: bool = True
    llm_timeout_seconds: float = 60.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

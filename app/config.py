"""Application settings from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    chat_model: str = "gpt-5.4-mini"
    fallback_chat_model: str = "gpt-4.1-mini"  # used once on outage-type errors; "" disables
    embedding_model: str = "text-embedding-3-small"
    moderation_model: str = "omni-moderation-latest"
    llm_timeout_seconds: float = 60.0

    database_path: str = "./data/app.db"
    chroma_path: str = "./data/chroma"
    chroma_collection: str = "getnet_kb"
    auto_ingest: bool = True
    rag_top_k: int = 8
    rag_min_similarity: float = 0.30

    planner_min_confidence: float = 0.55
    max_clarifications: int = 1
    history_window: int = 10
    moderation_enabled: bool = True

    jwt_secret: str = ""
    jwt_issuer: str = "getnet-support"
    jwt_ttl_minutes: int = 60
    enable_dev_tokens: bool = False
    rate_limit_per_minute: int = 30

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

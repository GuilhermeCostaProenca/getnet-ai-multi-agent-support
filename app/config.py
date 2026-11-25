"""Settings from environment / .env (CON-02: Anthropic only)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (ADR-02)
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    fallback_model: str = "claude-sonnet-5-5"  # RES-01: one retry on connection/timeout/5xx; "" disables
    llm_effort: str = "low"
    llm_max_tokens: int = 8000
    llm_timeout_seconds: float = 90.0

    # Storage / knowledge (ADR-03, ADR-06)
    database_path: str = "data/getnet.db"
    auto_ingest: bool = True
    rag_top_k: int = 6
    rag_candidates: int = 20
    rerank_enabled: bool = True

    # Orchestration (ORC-03, MEM-02)
    planner_min_confidence: float = 0.55
    max_clarifications: int = 1
    history_window: int = 10
    tool_timeout_seconds: float = 5.0

    # Security (SEC-01..03)
    jwt_secret: str = ""
    jwt_issuer: str = "getnet-support"
    jwt_ttl_minutes: int = 60
    enable_dev_tokens: bool = False
    rate_limit_per_minute: int = 30

    # Observability (OBS-03)
    otel_enabled: bool = False
    otel_endpoint: str = ""  # OTLP/HTTP traces endpoint, e.g. http://collector:4318/v1/traces
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Smart Learning System"
    app_env: str = "development"
    app_version: str = "0.1.0"

    database_url: str = (
        "postgresql+psycopg://smart_learning:change-me-before-deployment"
        "@postgres:5432/smart_learning"
    )
    qdrant_url: str = "http://qdrant:6333"
    ollama_base_url: str = "http://ollama:11434"
    ollama_chat_model: str = "gpt-oss:20b"
    ollama_think_mode: Literal["low", "medium", "high", "off"] = "low"
    ollama_num_threads: int = Field(default=0, ge=0, le=256)
    ollama_embed_model: str = "nomic-embed-text"
    ollama_embed_digest: str = ""
    qdrant_collection_prefix: str = "smart_learning_chunks"
    upload_root: str = "/data/uploads"
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=500, ge=1)
    max_pdf_characters: int = Field(default=2_000_000, ge=1000)
    embedding_batch_size: int = Field(default=4, ge=1, le=32)
    retrieval_limit: int = Field(default=8, ge=1, le=30)
    retrieval_score_threshold: float = Field(default=0.55, ge=0, le=1)
    rag_citation_example: bool = False
    rag_structured_output: bool = True
    rag_citation_attempts: int = Field(default=2, ge=1, le=3)
    worker_lease_seconds: int = Field(default=300, ge=30, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    chat_queue_capacity: int = Field(default=4, ge=1, le=64)
    chat_queue_user_capacity: int = Field(default=2, ge=1, le=64)
    chat_queue_wait_seconds: float = Field(default=60, gt=0, le=600)
    chat_generation_seconds: float = Field(default=150, gt=0, le=600)
    chat_history_messages: int = Field(default=12, ge=0, le=100)
    chat_context_tokens: int = Field(default=4096, ge=512, le=131072)
    chat_predict_tokens: int = Field(default=512, ge=32, le=8192)

    app_origin: str = "https://smart-learning.test"
    allowed_origins: str = (
        "https://smart-learning.test,http://127.0.0.1:8080,http://localhost:5173"
    )
    session_cookie_name: str = "smart_learning_session"
    csrf_cookie_name: str = "smart_learning_csrf"
    session_cookie_secure: bool = True
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    login_account_attempts: int = Field(default=10, ge=1, le=10000)
    login_global_attempts: int = Field(default=60, ge=1, le=10000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    login_max_concurrent: int = Field(default=2, ge=1, le=16)

    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    worker_poll_seconds: float = Field(default=15.0, ge=1, le=300)

    @property
    def allowed_origin_set(self) -> frozenset[str]:
        origins = {
            origin.strip().rstrip("/")
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        }
        origins.add(self.app_origin.rstrip("/"))
        return frozenset(origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()

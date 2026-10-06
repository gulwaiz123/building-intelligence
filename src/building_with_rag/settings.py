"""Application settings loaded from environment (.env is untracked; secrets never committed)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    mongodb_uri: str = ""
    voyage_api_key: str = ""
    capstone_api_key: str = ""
    generation_api_base_url: str = ""
    generation_api_key: str = ""
    mongodb_db_name: str = "building_with_rag"
    mongodb_test_db_name: str = "building_with_rag_test"
    webui_demo_caller_id: str = "demo-public"
    generation_model_name: str = "gpt-4o-mini"
    rerank_api_base_url: str = "https://api.voyageai.com/v1"
    rerank_model_name: str = "rerank-2.5"
    rerank_request_timeout_seconds: int = 30
    rerank_candidate_limit: int = 20
    rerank_send_limit: int = 10
    rerank_return_limit: int = 5


def get_settings() -> Settings:
    """Return cached settings instance (created on first call)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


_settings: Settings | None = None

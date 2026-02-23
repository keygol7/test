from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    app_name: str = "News Situation API"
    environment: str = "development"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/news_dashboard"
    )
    cors_origins_csv: str = ""
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_email: str = ""

    # LLM Categorization settings
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_model: str = ""
    categorizer_enabled: bool = False
    categorizer_interval_minutes: int = 5
    categorizer_batch_size: int = 50
    categorizer_relevance_threshold: float = 0.3
    categorizer_uncategorized_since_hours: int = 0
    categorizer_discovery_limit: int = 1000
    categorizer_discovery_since_hours: int = 336
    categorizer_backfill_enabled: bool = True
    categorizer_backfill_chunk_size: int = 500
    categorizer_backfill_write_batch_size: int = 50
    categorizer_backfill_max_situations_per_cycle: int = 20

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_csv.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

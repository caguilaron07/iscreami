"""Application settings with Pydantic v2."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = Field(
        default="postgresql+psycopg://localhost:5432/iscreami",
        description="PostgreSQL database connection URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        # Strip surrounding quotes that get included when copy-pasting env vars
        v = v.strip().strip('"').strip("'")
        # Normalize postgres:// and postgresql:// → postgresql+psycopg://
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v
    cors_origins: str = Field(
        default="*",
        description="Comma-separated list of allowed CORS origins",
    )
    serving_size_g: float = Field(
        default=66.0,
        description="Default serving size in grams",
    )
    anthropic_api_key: str | None = None


settings = Settings()

"""
app.config
----------
Centralized, validated application configuration. All values are read from
environment variables (populated by the .env file that install.sh generates
and docker-compose injects into the backend_api container). Nothing in this
module reads secrets from disk paths that could end up inside a database
backup or a Git commit.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings, validated once at process startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    admin_database_url: str = Field(
        ...,
        description="SQLAlchemy connection URL for the platform's own admin/metadata database.",
        alias="ADMIN_DATABASE_URL",
    )
    genbi_master_encryption_key: str = Field(
        ...,
        description="Fernet key used to encrypt/decrypt stored target-DB credentials and LLM API keys.",
        alias="GENBI_MASTER_ENCRYPTION_KEY",
    )
    genbi_jwt_signing_key: str = Field(
        ...,
        description="Symmetric signing key for Admin Panel and OpenUI session JWTs.",
        alias="GENBI_JWT_SIGNING_KEY",
    )
    jwt_algorithm: str = Field(default="HS256", alias="GENBI_JWT_ALGORITHM")
    jwt_access_token_expiry_minutes: int = Field(default=60, alias="GENBI_JWT_EXPIRY_MINUTES")

    redis_url: str = Field(default="redis://redis_cache:6379/0", alias="REDIS_URL")

    cors_allowed_origins: str = Field(
        default="*",
        description="Comma-separated list of allowed CORS origins for the two frontends.",
        alias="GENBI_CORS_ALLOWED_ORIGINS",
    )

    max_query_result_rows: int = Field(default=500, alias="GENBI_MAX_QUERY_RESULT_ROWS")
    default_connect_timeout_seconds: int = Field(default=10, alias="GENBI_DEFAULT_CONNECT_TIMEOUT_SECONDS")
    default_statement_timeout_seconds: int = Field(default=30, alias="GENBI_DEFAULT_STATEMENT_TIMEOUT_SECONDS")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings accessor. Using lru_cache means Settings() is only
    constructed (and validated) once per process, and every module imports
    the same validated instance via this function rather than instantiating
    Settings() directly.
    """
    return Settings()

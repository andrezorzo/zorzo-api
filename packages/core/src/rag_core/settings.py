"""Settings every app shares, validated at startup (LLD "Configuration")."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    # hide_input_in_errors: a startup error must never print DATABASE_URL or another secret.
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    app_env: Literal["local", "production"] = "production"
    domain: str = Field(min_length=1, description="Name of the domain_<name> package to load (ADR 0011)")
    database_url: str = Field(min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def _postgres_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
            raise ValueError("must be a postgresql://user:password@host:port/name URL")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

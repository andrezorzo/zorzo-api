"""Settings every app shares, validated at startup (LLD "Configuration")."""

from pathlib import Path
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
    limits_file: Path = Path("config/limits.yaml")
    # `fake` is deterministic and accepted only with APP_ENV=local (checked where adapters are built).
    embedder: Literal["ollama", "fake"] = "ollama"
    embed_model_tag: str = "bge-m3:567m"
    ollama_url: str = "http://localhost:11434"
    reranker: Literal["local", "fake"] = "local"
    # Resolved when settings are built, not at import, so an unusual HOME is a config error, not a crash.
    rerank_cache_dir: Path = Field(default_factory=lambda: Path.home() / ".cache" / "zorzo-rerank")

    @field_validator("database_url")
    @classmethod
    def _postgres_url(cls, value: str) -> str:
        parts = urlsplit(value)
        scheme = parts.scheme.split("+", 1)[0]  # postgresql+psycopg is fine
        has_host = bool(parts.hostname) or "host=" in parts.query  # host= in the query: Unix socket
        # A fragment means an unencoded '#' (usually in the password) cut the URL short.
        if scheme not in ("postgresql", "postgres") or not has_host or parts.fragment:
            raise ValueError("must be a postgresql://user:password@host:port/name URL, percent-encoded")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

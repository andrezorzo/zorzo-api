"""API settings, validated at startup (LLD "Configuration")."""

from pydantic import Field, SecretStr
from rag_core.settings import AppSettings


class ApiSettings(AppSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)
    # Secrets: required, never logged (LLD "Environment variables"). Locally, Turnstile's always-pass
    # test secret.
    turnstile_secret: SecretStr = Field(min_length=1)
    salt_seed: SecretStr = Field(min_length=32, description="Seed of the daily IP salt")

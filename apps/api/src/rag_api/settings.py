"""API settings, validated at startup (LLD "Configuration")."""

from pydantic import Field, SecretStr, model_validator
from rag_core.settings import AppSettings


class ApiSettings(AppSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)
    # Secrets: required, never logged (LLD "Environment variables"). Locally, Turnstile's always-pass
    # test secret.
    turnstile_secret: SecretStr = Field(min_length=1)
    salt_seed: SecretStr = Field(min_length=32, description="Seed of the daily IP salt")

    @model_validator(mode="after")
    def _real_turnstile_secret_in_production(self) -> "ApiSettings":
        # Cloudflare's test secrets (1x…, 2x…, 3x…AA) would turn the bot check off or break it in production.
        secret = self.turnstile_secret.get_secret_value()
        if self.app_env == "production" and secret.endswith("0000000000000000000000000000000AA"):
            raise ValueError("TURNSTILE_SECRET is a Cloudflare test secret; production needs the real one")
        return self

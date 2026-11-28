"""API settings, validated at startup (LLD "Configuration")."""

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from rag_core.settings import AppSettings

PUBLISHED_LOCAL_SEED = "local-salt-seed-change-me-0123456789"  # infra/.env.example, apps/api/AGENTS.md
PUBLISHED_LOCAL_DB_PASSWORD = "zorzo"  # POSTGRES_PASSWORD in infra/.env.example


class ApiSettings(AppSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)
    # Secrets: required, never logged (LLD "Environment variables"). Locally, Turnstile's always-pass
    # test secret.
    turnstile_secret: SecretStr = Field(min_length=1)
    salt_seed: SecretStr = Field(min_length=32, description="Seed of the daily IP salt")
    # Models config; unset: config/models.yaml, or config/models.local.yaml with APP_ENV=local.
    models_file: Path | None = None
    # Model providers: required in production by the providers a chain uses (checked when they are built).
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    google_ai_api_key: SecretStr | None = None
    # Alerts (LLD "6.10 Alerts"): required in production; locally the alerter only logs.
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    # LangSmith (LLD "Configuration"): a key turns it on; without one the API sends nothing. The endpoint is
    # LangSmith's own unless set (the tests point it at a fake).
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str | None = None
    langsmith_endpoint: str | None = None

    @field_validator(
        "openrouter_api_key",
        "groq_api_key",
        "nvidia_api_key",
        "google_ai_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
        "langsmith_api_key",
        "langsmith_project",
        "langsmith_endpoint",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # Compose passes `${VAR:-}` as an empty string when the variable is unset.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def models_path(self) -> Path:
        if self.models_file is not None:
            return self.models_file
        return Path("config/models.local.yaml" if self.app_env == "local" else "config/models.yaml")

    def provider_keys(self) -> dict[str, str | None]:
        """Each provider key by its variable name, as the models config's `key_env` names it."""
        keys = {
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "GROQ_API_KEY": self.groq_api_key,
            "NVIDIA_API_KEY": self.nvidia_api_key,
            "GOOGLE_AI_API_KEY": self.google_ai_api_key,
        }
        return {name: key.get_secret_value() if key else None for name, key in keys.items()}

    def secret_values(self) -> list[str]:
        """Every secret value the app holds, for the trace redactor. A setting that adds a secret (such as
        the canary of F01-T08) adds it here."""
        secrets = [
            self.turnstile_secret,
            self.salt_seed,
            self.openrouter_api_key,
            self.groq_api_key,
            self.nvidia_api_key,
            self.google_ai_api_key,
            self.telegram_bot_token,
            self.telegram_chat_id,
            self.langsmith_api_key,
        ]
        # The published local password is public, and would redact every "zorzo" of a local trace.
        passwords = [p for p in self._database_passwords() if p != PUBLISHED_LOCAL_DB_PASSWORD]
        return [*(s.get_secret_value() for s in secrets if s is not None), *passwords]

    def _database_passwords(self) -> list[str]:
        """The database password as `DATABASE_URL` writes it (percent-encoded) and as the database knows it,
        from the user info and from a `?password=` in the query."""
        parts = urlsplit(self.database_url)
        written = [parts.password] if parts.password else []
        written += [pair.partition("=")[2] for pair in parts.query.split("&") if pair.startswith("password=")]
        known = [unquote(w) for w in written] + parse_qs(parts.query).get("password", [])
        return [p for p in dict.fromkeys([*written, *known]) if p]

    @model_validator(mode="after")
    def _real_secrets_in_production(self) -> "ApiSettings":
        if self.app_env != "production":
            return self
        # Cloudflare's test secrets (1x…, 2x…, 3x…AA) would turn the bot check off or break it.
        if self.turnstile_secret.get_secret_value().endswith("0000000000000000000000000000000AA"):
            raise ValueError("TURNSTILE_SECRET is a Cloudflare test secret; production needs the real one")
        # The published local seed would let anyone with the repository reverse the hashed IPs.
        if self.salt_seed.get_secret_value().strip() == PUBLISHED_LOCAL_SEED:
            raise ValueError("SALT_SEED is the published local example; production needs its own secret")
        # The compose file's database password, published in the repository, is no password in production.
        if PUBLISHED_LOCAL_DB_PASSWORD in self._database_passwords():
            raise ValueError(
                "DATABASE_URL has the published local database password; production needs its own"
            )
        if not (self.telegram_bot_token and self.telegram_chat_id):
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required in production (alerts)")
        return self

"""API settings, validated at startup (LLD "Configuration")."""

from pydantic import Field
from zorzo_core.settings import AppSettings


class ApiSettings(AppSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)

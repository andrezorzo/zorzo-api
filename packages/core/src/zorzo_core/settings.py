"""Settings every app shares, validated at startup (LLD "Configuration")."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["local", "production"] = "production"
    domain: str = Field(min_length=1, description="Name of the domain_<name> package to load (ADR 0011)")
    database_url: str = Field(min_length=1)

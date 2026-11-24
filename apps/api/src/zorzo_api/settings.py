"""API settings, validated at startup (LLD "Configuration")."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["local", "production"] = "production"
    domain: str = Field(min_length=1)
    database_url: str = Field(min_length=1)

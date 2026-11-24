"""Ingest settings, validated at startup. The pipeline arrives with F01-T02."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["local", "production"] = "production"
    domain: str = Field(min_length=1)
    database_url: str = Field(min_length=1)

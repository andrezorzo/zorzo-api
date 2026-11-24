"""API settings, validated at startup (LLD "Configuration")."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from rag_core.settings import AppSettings


class ApiSettings(AppSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=0, le=65535)
    reranker: Literal["local", "fake"] = "local"
    rerank_cache_dir: Path = Path.home() / ".cache" / "zorzo-rerank"

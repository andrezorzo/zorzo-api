"""Ingest settings, validated at startup (LLD "Configuration")."""

from pathlib import Path

from rag_core.settings import AppSettings


class IngestSettings(AppSettings):
    data_dir: Path = Path("var/data")
    gitleaks_binary: str = "gitleaks"

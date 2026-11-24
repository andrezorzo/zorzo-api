"""Domain loading (ADR 0011): `DOMAIN=<name>` selects the `domain_<name>` package.

The core never imports a domain statically; the apps call `load_domain` at startup.
"""

import importlib
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class DomainError(RuntimeError):
    """The domain package is missing or its config is invalid."""


class DomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    languages: tuple[str, ...]
    # Exact strings the PII pass lets through, e.g. the owner's public email (LLD "6.1 Corpus version").
    pii_allowlist: tuple[str, ...] = ()


class Example(BaseModel):
    """An example question on the first screen; `done.suggestion` uses the same id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    lang: str
    text: str


def load_domain(name: str) -> DomainConfig:
    package = f"domain_{name}"
    try:
        importlib.import_module(package)
    except ModuleNotFoundError as exc:
        if exc.name == package:
            raise DomainError(f"domain package {package!r} not found (DOMAIN={name!r})") from exc
        raise DomainError(f"domain package {package!r} failed to import: {exc}") from exc
    except Exception as exc:
        raise DomainError(f"domain package {package!r} failed to import: {exc}") from exc
    try:
        raw = yaml.safe_load(files(package).joinpath("domain.yaml").read_text())
        return DomainConfig.model_validate(raw)
    except Exception as exc:
        raise DomainError(f"invalid domain config in {package}/domain.yaml: {exc}") from exc


def load_examples(name: str) -> list[Example]:
    package = f"domain_{name}"
    try:
        raw = yaml.safe_load(files(package).joinpath("examples.yaml").read_text())
        return [Example.model_validate(item) for item in raw]
    except Exception as exc:
        raise DomainError(f"invalid example questions in {package}/examples.yaml: {exc}") from exc


def corpus_dir(name: str) -> Path:
    """The domain package's `corpus/` folder, the ingest command's default source."""
    return Path(str(files(f"domain_{name}").joinpath("corpus")))

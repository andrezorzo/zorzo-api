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


def _package(name: str) -> str:
    return f"domain_{name}"


def _read_yaml(name: str, filename: str) -> object:
    return yaml.safe_load(files(_package(name)).joinpath(filename).read_text())


def load_domain(name: str) -> DomainConfig:
    package = _package(name)
    try:
        importlib.import_module(package)
    except ModuleNotFoundError as exc:
        if exc.name == package:
            raise DomainError(f"domain package {package!r} not found (DOMAIN={name!r})") from exc
        raise DomainError(f"domain package {package!r} failed to import: {exc}") from exc
    except Exception as exc:
        raise DomainError(f"domain package {package!r} failed to import: {exc}") from exc
    try:
        return DomainConfig.model_validate(_read_yaml(name, "domain.yaml"))
    except Exception as exc:
        raise DomainError(f"invalid domain config in {package}/domain.yaml: {exc}") from exc


def load_examples(name: str) -> list[Example]:
    try:
        raw = _read_yaml(name, "examples.yaml")
        if not isinstance(raw, list):
            raise ValueError("expected a list of questions")
        return [Example.model_validate(item) for item in raw]
    except Exception as exc:
        raise DomainError(f"invalid example questions in {_package(name)}/examples.yaml: {exc}") from exc


def corpus_dir(name: str) -> Path:
    """The domain package's `corpus/` folder, the ingest command's default source."""
    return Path(str(files(_package(name)).joinpath("corpus")))

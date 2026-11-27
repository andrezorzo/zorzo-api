"""Domain loading (ADR 0011): `DOMAIN=<name>` selects the `domain_<name>` package.

The core never imports a domain statically; the apps call `load_domain` at startup.
"""

import importlib
from importlib.resources import files
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from rag_core.ingest.frontmatter import DOCUMENT_KINDS


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


class Intent(BaseModel):
    """One intent `route` may return (`intents.yaml`): what its retrieval reads and what the run does."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kinds: tuple[str, ...] = ()  # document kinds its retrieval reads; empty means every kind
    action: Literal["answer", "decline"] = "answer"

    @field_validator("kinds")
    @classmethod
    def _known_kinds(cls, kinds: tuple[str, ...]) -> tuple[str, ...]:
        # A misspelled kind would match no document, and the intent would abstain on every question.
        unknown = sorted(set(kinds) - set(DOCUMENT_KINDS))
        if unknown:
            raise ValueError(f"unknown document kinds {unknown}; known: {list(DOCUMENT_KINDS)}")
        return kinds


def load_intents(name: str) -> dict[str, Intent]:
    try:
        raw = _read_yaml(name, "intents.yaml")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("expected a mapping of intent names")
        # YAML 1.1 reads `no`, `off` or `null` as non-strings; quote such a name instead of losing it.
        odd = [key for key in raw if not isinstance(key, str)]
        if odd:
            raise ValueError(f"intent names must be strings (quote them): {odd}")
        return {key: Intent.model_validate(value or {}) for key, value in raw.items()}
    except Exception as exc:
        raise DomainError(f"invalid intents in {_package(name)}/intents.yaml: {exc}") from exc


class Prompts(BaseModel):
    """The agent's prompt templates (`prompts/<stage>.md`), with `$slot` placeholders the core fills."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: str
    assess: str
    reformulate: str
    answer: str
    judge: str
    repair: str


def load_prompts(name: str) -> Prompts:
    try:
        folder = files(_package(name)).joinpath("prompts")
        return Prompts(
            **{field: folder.joinpath(f"{field}.md").read_text() for field in Prompts.model_fields}
        )
    except Exception as exc:
        raise DomainError(f"missing or invalid prompts in {_package(name)}/prompts: {exc}") from exc


def corpus_dir(name: str) -> Path:
    """The domain package's `corpus/` folder, the ingest command's default source."""
    return Path(str(files(_package(name)).joinpath("corpus")))

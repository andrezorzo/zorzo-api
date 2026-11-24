"""Domain loading (ADR 0011): `DOMAIN=<name>` selects the `domain_<name>` package.

The core never imports a domain statically; the apps call `load_domain` at startup.
"""

import importlib
from importlib.resources import files

import yaml
from pydantic import BaseModel, ConfigDict


class DomainError(RuntimeError):
    """The domain package is missing or its config is invalid."""


class DomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    languages: tuple[str, ...]


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

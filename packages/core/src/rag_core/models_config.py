"""Models config (LLD "Configuration", models config): providers and the four stages' chains.

`config/models.yaml` in production, `config/models.local.yaml` with `APP_ENV=local` (`MODELS_FILE` overrides);
an invalid file stops the app.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag_core.limits import Limits

StageName = Literal["route", "rerank", "answer", "judge"]

_STRICT = ConfigDict(extra="forbid", frozen=True)
# `${NAME}` or `${NAME:-default}` in a provider's base URL, read from the environment at load.
_VARIABLE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


class Price(BaseModel):
    model_config = _STRICT
    input_per_mtok: float = Field(ge=0)  # US$ per million tokens
    output_per_mtok: float = Field(ge=0)


class Entry(BaseModel):
    model_config = _STRICT
    provider: str
    model: str = Field(min_length=1)
    paid: bool = False
    price: Price | None = None

    @model_validator(mode="after")
    def _paid_has_price(self) -> Self:
        if self.paid and self.price is None:
            raise ValueError(f"paid entry {self.model} needs a price")
        return self


class Stage(BaseModel):
    model_config = _STRICT
    max_tokens: int = Field(gt=0)  # output cap of every entry; the budget estimate uses it
    chain: list[Entry]  # tried in order: the free entries on the Router, then the paid one (ADR 0016)

    @property
    def free(self) -> list[Entry]:
        return [e for e in self.chain if not e.paid]

    @property
    def paid(self) -> Entry | None:
        return next((e for e in self.chain if e.paid), None)


ProviderKind = Literal["openrouter", "openai"]


class Provider(BaseModel):
    """An endpoint of the chains: `openrouter`, whose reported cost is read, or any other OpenAI-compatible
    one (`openai`: Groq, NVIDIA, Google, Ollama)."""

    model_config = _STRICT
    base_url: str = Field(pattern=r"^https?://[^\s]+$")
    key_env: str | None = None  # absent for a keyless provider (Ollama)
    kind: ProviderKind = "openai"

    @model_validator(mode="before")
    @classmethod
    def _resolve_url_then_kind(cls, data: Any) -> Any:
        """Reads `${VAR}`s in the base URL, then defaults the kind from the resolved URL."""
        if isinstance(data, dict) and isinstance(data.get("base_url"), str):
            data = {**data, "base_url": _VARIABLE.sub(_variable, data["base_url"])}
        if isinstance(data, dict) and "kind" not in data and "openrouter.ai" in str(data.get("base_url", "")):
            return {**data, "kind": "openrouter"}
        return data


def _variable(match: re.Match[str]) -> str:
    name, default = match.group(1), match.group(2)
    value = os.environ.get(name) or default  # set but empty counts as unset, as in the shell
    if value is None:
        raise ValueError(f"base_url needs the environment variable {name}")
    return value


class Stages(BaseModel):
    model_config = _STRICT
    route: Stage
    rerank: Stage
    answer: Stage
    judge: Stage


class ModelsConfig(BaseModel):
    model_config = _STRICT
    providers: dict[str, Provider]
    stages: Stages

    def stage(self, name: StageName) -> Stage:
        stage: Stage = getattr(self.stages, name)
        return stage

    @model_validator(mode="after")
    def _chains_are_usable(self) -> Self:
        for name in ("route", "rerank", "answer", "judge"):
            stage = self.stage(name)
            if not stage.chain and name != "rerank":  # rerank alone may be local-only
                raise ValueError(f"stage {name} needs at least one chain entry")
            for entry in stage.chain:
                if entry.provider not in self.providers:
                    raise ValueError(f"stage {name} uses unknown provider {entry.provider}")
            paid = [i for i, e in enumerate(stage.chain) if e.paid]
            if name == "rerank" and paid:
                raise ValueError("stage rerank can't have a paid entry")
            if len(paid) > 1 or (paid and paid[0] != len(stage.chain) - 1):
                raise ValueError(f"stage {name}: one paid entry at most, last in the chain")
        return self


def entry_id(stage: StageName, entry: Entry) -> str:
    """The entry's name on the Router and in the logs: `<stage>:<provider>/<model>`."""
    return f"{stage}:{entry.provider}/{entry.model}"


def load_models(path: Path) -> ModelsConfig:
    return ModelsConfig.model_validate(yaml.safe_load(path.read_text()))


def config_version(models: ModelsConfig, limits: Limits) -> str:
    """SHA-256 of the canonical JSON of the parsed config: comments, key order and formatting don't count."""
    content = {"models": models.model_dump(mode="json"), "limits": limits.model_dump(mode="json")}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()

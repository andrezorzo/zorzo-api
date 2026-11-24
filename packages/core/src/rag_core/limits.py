"""Rule values from `config/limits.yaml` (LLD "Values and configuration"); an invalid file stops the app."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_question_chars: int = Field(gt=0)
    ping_interval: float = Field(gt=0)
    retrieval_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rerank_top_n: int = Field(gt=0)
    degraded_max_passages: int = Field(gt=0)
    chunk_tokens: int = Field(gt=0)
    chunk_overlap: float = Field(ge=0, lt=1)
    search_min_similarity: float = Field(ge=-1, le=1)


def load_limits(path: Path) -> Limits:
    return Limits.model_validate(yaml.safe_load(path.read_text()))

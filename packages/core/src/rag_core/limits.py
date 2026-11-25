"""Rule values from `config/limits.yaml` (LLD "Values and configuration"); an invalid file stops the app."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_question_chars: int = Field(gt=0)
    max_body_bytes: int = Field(gt=0)
    ping_interval: float = Field(gt=0)
    request_deadline: float = Field(gt=0)
    retrieval_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rerank_top_n: int = Field(gt=0)
    degraded_max_passages: int = Field(gt=0)
    chunk_tokens: int = Field(gt=0)
    chunk_overlap: float = Field(ge=0, lt=1)
    search_min_similarity: float = Field(ge=-1, le=1)
    # Rate limit (LLD "6.7 Rate limit"); the burst window is shared by the IP and session buckets.
    rate_ip_burst: int = Field(ge=1)
    rate_ip_window: float = Field(gt=0)
    rate_ip_daily: int = Field(ge=1)
    rate_session_burst: int = Field(ge=1)
    rate_session_daily: int = Field(ge=1)
    timeout_turnstile: float = Field(gt=0)


def load_limits(path: Path) -> Limits:
    return Limits.model_validate(yaml.safe_load(path.read_text()))

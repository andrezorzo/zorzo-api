"""Rule values from `config/limits.yaml` (LLD "Values and configuration"); an invalid file stops the app."""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Model gateway (LLD "6.2 Daily budget", "6.6 Circuit breaker", "6.8 Free quota", "6.10 Alerts").
    timeout_route: float = Field(gt=0)
    timeout_rerank: float = Field(gt=0)
    timeout_answer: float = Field(gt=0)
    timeout_judge: float = Field(gt=0)
    daily_budget_usd: float = Field(ge=0)
    budget_paid_cutoff: float = Field(gt=0, le=1)
    request_cost_cap_usd: float = Field(ge=0)
    breaker_cooldown: float = Field(gt=0)
    alert_repeat_after: float = Field(gt=0)
    # Agent run (LLD "6.3 Agent run").
    answer_reserve: float = Field(gt=0)
    max_reformulate: int = Field(ge=0)
    max_repair: int = Field(ge=0)
    recursion_limit: int = Field(gt=0)
    context_max_tokens: int = Field(gt=0)
    # Off until they are calibrated on the eval set at M3: abstain only on no passage, always assess.
    relevance_floor: float | None = None
    assess_skip_score: float | None = None

    @model_validator(mode="after")
    def _reserve_leaves_time_before_the_answer(self) -> Self:
        if self.answer_reserve >= self.request_deadline:
            raise ValueError("answer_reserve must be shorter than request_deadline")
        return self


def load_limits(path: Path) -> Limits:
    return Limits.model_validate(yaml.safe_load(path.read_text()))

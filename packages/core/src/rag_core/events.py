"""Stream events (LLD "Stream events"): one Pydantic model per event, the source of the SSE contract.

Producers emit these models through the TraceSink; the SSE route writes each as `event: <kind>` and
`data: <payload JSON>`. `python -m rag_core.events` writes `contracts/sse-v1.schema.json` from them, and a
test fails when the committed file drifts.
"""

import functools
import json
import operator
import sys
from typing import Annotated, Any, ClassVar, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model

StageName = Literal[
    "guard",
    "route",
    "cache",
    "retrieve",
    "rerank",
    "assess",
    "reformulate",
    "answer",
    "verify",
    "repair",
    "finish",
]
DegradedReason = Literal["models_unavailable", "budget_exhausted", "quota_exhausted", "deadline"]


class StreamEvent(BaseModel):
    """Base of every event; `kind` is the SSE event name."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ClassVar[str]

    def payload(self) -> str:
        return self.model_dump_json()


class Stage(StreamEvent):
    kind = "stage"
    name: StageName
    status: Literal["start", "end"]
    ms: int | None = Field(description="Set on `end`, null on `start`")


class Guard(StreamEvent):
    kind = "guard"
    layer: str
    label: str
    action: str


class Hit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(description="Chunk id, as in corpus.json of the same version")
    title: str
    score: float


class Retrieval(StreamEvent):
    kind = "retrieval"
    hits: list[Hit]
    corpus_version: int


class Model(StreamEvent):
    kind = "model"
    stage: str
    model: str
    provider: str
    fallback_reason: str | None = None


class Token(StreamEvent):
    kind = "token"
    text: str


class Claim(StreamEvent):
    kind = "claim"
    i: int
    text: str
    sources: list[int] = Field(description="Chunk ids")
    verdict: Literal["supported", "partial", "unsupported"]


class Usage(StreamEvent):
    kind = "usage"
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


class Blocked(StreamEvent):
    kind = "blocked"
    layer: str
    category: str
    message: str


class Degraded(StreamEvent):
    kind = "degraded"
    mode: Literal["search_only"] = "search_only"
    reason: DegradedReason


class Error(StreamEvent):
    kind = "error"
    code: str
    message: str


class Done(StreamEvent):
    kind = "done"
    trace_id: UUID
    cached: bool
    abstained: bool
    suggestion: str | None = Field(
        description="Id of the closest example question, on an abstention or decline"
    )


EVENTS: tuple[type[StreamEvent], ...] = (
    Stage,
    Guard,
    Retrieval,
    Model,
    Token,
    Claim,
    Usage,
    Blocked,
    Degraded,
    Error,
    Done,
)

ORDERING = (
    "SSE frames `event: <name>` and `data: <JSON>`; `: ping` comments keep the connection alive. "
    "Ordering: `done` is always last, sent exactly once unless the connection drops; `blocked`, `degraded` "
    "and `error` are followed only by `done`, except that `degraded` sends its `retrieval` hits first; every "
    "`token` comes before the first `claim`; a cached answer sends `stage`, the cached `claim` events, "
    "`usage` with cost 0 and `done {cached: true}`, with no `token`. v1 freezes at launch."
)


def sse_schema() -> dict[str, Any]:
    """The JSON Schema of one stream event as `{event, data}`."""
    wrappers: list[Any] = [
        create_model(
            f"{cls.__name__}Event",
            __config__=ConfigDict(extra="forbid"),
            event=(cast(Any, Literal[cls.kind]), ...),
            data=(cls, ...),
        )
        for cls in EVENTS
    ]
    union: Any = Annotated[functools.reduce(operator.or_, wrappers), Field(discriminator="event")]
    schema: dict[str, Any] = TypeAdapter[Any](union).json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://zorzo.dev.br/contracts/sse-v1.schema.json",
        "title": "zorzo.dev.br stream events, v1",
        "description": ORDERING,
        **schema,
    }


if __name__ == "__main__":
    sys.stdout.write(json.dumps(sse_schema(), indent=2, ensure_ascii=False) + "\n")

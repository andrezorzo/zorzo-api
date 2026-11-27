"""Routing (LLD "6.3 Agent run"): one structured-output call classifies the question and rewrites it to stand
on its own."""

import functools
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, create_model

from rag_core.services.gateway import Completion, ModelGateway, RequestSpend


class RouteResult(BaseModel):
    """The route output; `route_schema` narrows `intent` to the domain's intents (ADR 0011)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    standalone_question: str
    language: str  # ISO 639-1
    follow_up: bool  # the question refers to earlier turns (F01-T07)
    public_ok: bool  # whether the question may appear on public pages; never changes the answer


@functools.cache
def route_schema(intents: tuple[str, ...]) -> type[RouteResult]:
    """The route output with `intent` limited to these names, so the model's structured output and the
    validation both know the domain's intents. Built once per set of names."""
    if not intents:
        raise ValueError("route needs at least one intent")
    intent: Any = Literal[intents]
    return create_model("RouteResult", __base__=RouteResult, intent=(intent, ...))


async def route(
    question: str,
    *,
    gateway: ModelGateway,
    prompt: str,
    schema: type[RouteResult],
    deadline_at: float,
    spend: RequestSpend,
) -> tuple[RouteResult, Completion]:
    """Raises `ModelsUnavailableError` when the `route` chain can't answer with the schema."""
    completion = await gateway.complete(
        "route",
        [{"role": "system", "content": prompt}, {"role": "user", "content": question}],
        schema=schema,
        deadline_at=deadline_at,
        spend=spend,
    )
    return cast(RouteResult, completion.parsed), completion

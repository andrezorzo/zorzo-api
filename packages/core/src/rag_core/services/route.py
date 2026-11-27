"""Routing (LLD "6.3 Agent run"): one structured-output call classifies the question and rewrites it to stand
on its own."""

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from rag_core.services.gateway import Completion, ModelGateway, RequestSpend

Intent = Literal["career_fact", "about_andre", "about_system", "projects_live", "contact", "off_topic"]


class RouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    standalone_question: str
    language: str  # ISO 639-1
    follow_up: bool  # the question refers to earlier turns (F01-T07)
    public_ok: bool  # whether the question may appear on public pages; never changes the answer


async def route(
    question: str, *, gateway: ModelGateway, prompt: str, deadline_at: float, spend: RequestSpend
) -> tuple[RouteResult, Completion]:
    """Raises `ModelsUnavailableError` when the `route` chain can't answer with the schema."""
    completion = await gateway.complete(
        "route",
        [{"role": "system", "content": prompt}, {"role": "user", "content": question}],
        schema=RouteResult,
        deadline_at=deadline_at,
        spend=spend,
    )
    return cast(RouteResult, completion.parsed), completion

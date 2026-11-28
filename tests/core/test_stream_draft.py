"""F01-T08 review: the answer call's usage and cost are recorded even when the canary arrives in its last
chunk (the run is blocked, but the trace still holds what the call cost)."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from rag_core.agent.context import AgentContext
from rag_core.agent.nodes import _CanaryLeakError, _stream_draft
from rag_core.domain import Intent, load_prompts
from rag_core.events import Model, StreamEvent
from rag_core.services.gateway import Completion, RequestSpend, Usage
from rag_core.services.guard import CanaryWatch
from rag_core.services.redact import Redactor

from tests.core.test_gateway import LIMITS

CANARY = "zorzo-canary-0123456789abcdefghijklmnop"
TAIL_CHARS = 4  # the last chunk completes the canary
TOKENS_IN, TOKENS_OUT, COST_USD = 321, 45, 0.000123


class Recorder:
    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    def record(self, event: StreamEvent) -> None:
        self.events.append(event)


async def test_the_answer_calls_usage_is_recorded_when_the_canary_completes_in_the_last_chunk() -> None:
    held, last = CANARY[:-TAIL_CHARS], CANARY[-TAIL_CHARS:]
    head = '{"claims": [{"text": "The marker is ' + held  # parsed when it arrives: a quote opened a string

    async def stream(*_: Any, **__: Any) -> AsyncIterator[str | Completion]:
        yield head
        yield last  # completes the canary: no quote, too short to be parsed on its own
        yield Completion("", None, Usage(TOKENS_IN, TOKENS_OUT), COST_USD, "test", "test/answer")

    sink = Recorder()
    context = AgentContext(
        gateway=cast(Any, SimpleNamespace(stream=stream)),
        embedder=cast(Any, None),
        store=cast(Any, None),
        rerankers=[],
        limits=LIMITS,
        prompts=load_prompts("zorzo", {"any": Intent(description="Any question.")}),
        intents={"any": Intent(description="Any question.")},
        languages=["en"],
        sink=sink,
        corpus_version=1,
        deadline_at=asyncio.get_running_loop().time() + 10,
        spend=RequestSpend(LIMITS.request_cost_cap_usd),
        classifier=cast(Any, None),
        defenses=cast(Any, None),
        suggestions=cast(Any, None),
        cache=cast(Any, None),
        canary=CanaryWatch(CANARY),
        trace_id=uuid4(),
        redactor=Redactor(),
    )
    with pytest.raises(_CanaryLeakError):
        await _stream_draft([], context)
    models = [e for e in sink.events if isinstance(e, Model)]
    assert [(m.tokens_in, m.tokens_out, m.cost_usd) for m in models] == [(TOKENS_IN, TOKENS_OUT, COST_USD)]
    assert (context.tokens_in, context.tokens_out, context.cost_usd) == (TOKENS_IN, TOKENS_OUT, COST_USD)

"""F01-T06 AC2: reaching LangGraph's recursion backstop is a bug; the run abstains and logs it."""

import asyncio
from typing import Any, cast
from uuid import uuid4

from langgraph.graph import START, StateGraph
from rag_core.agent.context import AgentContext
from rag_core.agent.graph import Agent, run_agent
from rag_core.agent.state import AgentState
from rag_core.domain import Intent, load_prompts
from rag_core.events import StreamEvent
from rag_core.services.gateway import RequestSpend
from structlog.testing import capture_logs

from tests.core.test_gateway import LIMITS


class Recorder:
    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    def record(self, event: StreamEvent) -> None:
        self.events.append(event)


def looping() -> Agent:
    async def again(state: AgentState) -> dict[str, Any]:
        return {"reformulated": state.get("reformulated", 0) + 1}

    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("again", again)
    graph.add_edge(START, "again")
    graph.add_edge("again", "again")
    agent: Agent = graph.compile()
    return agent


async def test_the_recursion_backstop_abstains_and_logs() -> None:
    sink = Recorder()
    context = AgentContext(
        gateway=cast(Any, None),
        embedder=cast(Any, None),
        store=cast(Any, None),
        rerankers=[],
        limits=LIMITS,
        prompts=load_prompts("zorzo"),
        intents={"any": Intent()},
        languages=["en"],
        sink=sink,
        corpus_version=1,
        deadline_at=asyncio.get_running_loop().time() + 10,
        spend=RequestSpend(LIMITS.request_cost_cap_usd),
    )
    with capture_logs() as logs:
        result = await run_agent("q", uuid4(), agent=looping(), context=context)
    assert result.outcome == "abstained"
    assert [e.kind for e in sink.events] == ["usage", "done"]
    assert sink.events[-1].model_dump()["outcome"] == "abstained"
    assert {"event": "recursion_limit", "log_level": "error"}.items() <= logs[0].items()

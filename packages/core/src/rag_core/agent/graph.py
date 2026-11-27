"""The bounded graph (ADR 0006, LLD "6.3 Agent run"): route → retrieve → rerank → assess → optional
reformulate → answer → verify → optional repair, with abstain, decline and search-only exits.

`run_agent` emits the whole stream after the gate, ending with `done`; it never raises.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import structlog
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from rag_core.agent import nodes
from rag_core.agent.context import AgentContext
from rag_core.agent.state import AgentState
from rag_core.events import Done, Outcome, Usage
from rag_core.services.search_only import emit_degraded, end_with_error
from rag_core.units import MS_PER_S

log = structlog.get_logger()

Agent = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]
Step = Literal["route", "retrieve", "assess", "reformulate", "answer", "verify", "repair", "search_only"]


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    public_ok: bool | None  # None when `route` didn't answer


def _after(next_step: Step) -> Any:
    """The edge out of a step: the run's end, search-only, or `next_step`."""

    def edge(state: AgentState) -> str:
        if "outcome" in state:
            return END
        if "degraded" in state:
            return "search_only"
        return next_step

    return edge


def _after_retrieve(state: AgentState) -> str:
    if "outcome" in state:
        return END
    # One pass: no second `assess` after a rewrite; strong evidence skips it.
    return "answer" if state.get("reformulated") or state.get("skip_assess") else "assess"


def _after_assess(state: AgentState) -> str:
    if "degraded" in state:
        return "search_only"
    return "reformulate" if state.get("reformulate") else "answer"


def _after_verify(state: AgentState) -> str:
    return END if "outcome" in state else "repair"


def build_graph() -> Agent:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("route", nodes.route_step)
    graph.add_node("retrieve", nodes.retrieve_step)
    graph.add_node("assess", nodes.assess_step)
    graph.add_node("reformulate", nodes.reformulate_step)
    graph.add_node("answer", nodes.answer_step)
    graph.add_node("verify", nodes.verify_step)
    graph.add_node("repair", nodes.repair_step)
    graph.add_node("search_only", nodes.search_only_step)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _after("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("retrieve", _after_retrieve, ["assess", "answer", END])
    graph.add_conditional_edges("assess", _after_assess, ["reformulate", "answer", "search_only"])
    graph.add_conditional_edges("reformulate", _after("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("answer", _after("verify"), ["verify", "search_only", END])
    graph.add_conditional_edges("verify", _after_verify, ["repair", END])
    graph.add_conditional_edges("repair", _after("verify"), ["verify", "search_only", END])
    graph.add_edge("search_only", END)
    return graph.compile()


async def run_agent(question: str, trace_id: UUID, *, agent: Agent, context: AgentContext) -> RunResult:
    """Runs the graph for one question and ends the stream with `usage` (unless search-only) and `done`."""
    started = time.perf_counter()
    state: dict[str, Any] = {}
    try:
        async with asyncio.timeout_at(context.deadline_at):
            async for chunk in agent.astream(
                {"question": question},
                context=context,
                config={"recursion_limit": context.limits.recursion_limit},
                stream_mode="values",
            ):
                state = chunk
        outcome: Outcome = state["outcome"]
    except GraphRecursionError:
        log.error("recursion_limit")  # a bug: the graph looped past its backstop
        outcome = "abstained"
    except TimeoutError:
        log.warning("deadline_exceeded", stage="agent")
        if context.drafted:
            outcome = "abstained"  # a draft is never published unverified
        else:
            emit_degraded(context.sink, context.passages, context.corpus_version, "deadline", context.limits)
            outcome = "degraded"
    except Exception:
        log.exception("agent_failed")
        end_with_error(context.sink, trace_id)
        return RunResult("error", _public_ok(state))
    if outcome != "degraded":  # `degraded` may be followed only by its passages and `done`
        context.sink.record(
            Usage(
                tokens_in=context.tokens_in,
                tokens_out=context.tokens_out,
                cost_usd=context.cost_usd,
                latency_ms=round((time.perf_counter() - started) * MS_PER_S),
            )
        )
    context.sink.record(Done(trace_id=trace_id, outcome=outcome, cached=False, suggestion=None))
    return RunResult(outcome, _public_ok(state))


def _public_ok(state: dict[str, Any]) -> bool | None:
    route = state.get("route")
    return route.public_ok if route is not None else None

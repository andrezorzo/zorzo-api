"""The bounded graph (ADR 0006, LLD "6.3 Agent run"): route → retrieve → rerank → assess → optional
reformulate → answer → verify → optional repair, with abstain, decline and search-only exits.

`run_agent` emits the whole stream after the gate, ending with `done`; it never raises.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from rag_core.agent import nodes
from rag_core.agent.context import AgentContext
from rag_core.agent.state import AgentState
from rag_core.events import Done, Outcome, Usage
from rag_core.services.stream import emit_degraded, end_with_error
from rag_core.units import MS_PER_S

log = structlog.get_logger()

Agent = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    public_ok: bool | None  # None when `route` didn't answer


def _edge(next_step: Callable[[AgentState], str]) -> Callable[[AgentState], str]:
    """The edge out of a step: the run's end when it set an outcome, search-only when it degraded,
    otherwise `next_step`."""

    def edge(state: AgentState) -> str:
        if "outcome" in state:
            return END
        if "degraded" in state:
            return "search_only"
        return next_step(state)

    return edge


def _to(step: str) -> Callable[[AgentState], str]:
    return _edge(lambda _: step)


# One pass: no second `assess` after a rewrite; strong evidence skips it.
_after_retrieve = _edge(lambda s: "answer" if s.get("reformulated") or s.get("skip_assess") else "assess")
_after_assess = _edge(lambda s: "reformulate" if s.get("reformulate") else "answer")
_after_verify = _to("repair")


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
    graph.add_conditional_edges("route", _to("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("retrieve", _after_retrieve, ["assess", "answer", "search_only", END])
    graph.add_conditional_edges("assess", _after_assess, ["reformulate", "answer", "search_only", END])
    graph.add_conditional_edges("reformulate", _to("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("answer", _to("verify"), ["verify", "search_only", END])
    graph.add_conditional_edges("verify", _after_verify, ["repair", "search_only", END])
    graph.add_conditional_edges("repair", _to("verify"), ["verify", "search_only", END])
    graph.add_edge("search_only", END)
    return graph.compile()


async def run_agent(question: str, trace_id: UUID, *, agent: Agent, context: AgentContext) -> RunResult:
    """Runs the graph for one question and ends the stream with `usage` (unless search-only) and `done`."""
    state: dict[str, Any] = {}
    deadline = asyncio.timeout_at(context.deadline_at)
    try:
        async with deadline:
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
        if not deadline.expired():  # a step's own timeout escaped it: a bug, not the request's deadline
            log.exception("agent_failed")
            end_with_error(context.sink, trace_id)
            return RunResult("error", _public_ok(state))
        log.warning("deadline_exceeded", stage="agent")
        if context.settled is not None:  # a step already sent the ending: keep it
            outcome = context.settled
        elif context.drafted:
            outcome = "abstained"  # a draft is never published unverified
        elif context.found:
            emit_degraded(
                context.sink,
                context.found,
                context.corpus_version,
                "deadline",
                context.limits.degraded_max_passages,
            )
            outcome = "degraded"
        else:  # nothing found in time: "no matching sources" would be untrue
            end_with_error(context.sink, trace_id)
            return RunResult("error", _public_ok(state))
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
                latency_ms=round(_elapsed(context) * MS_PER_S),
            )
        )
    context.sink.record(Done(trace_id=trace_id, outcome=outcome, cached=False, suggestion=None))
    return RunResult(outcome, _public_ok(state))


def _elapsed(context: AgentContext) -> float:
    """Seconds since the request started: its deadline is `REQUEST_DEADLINE` after the start."""
    started = context.deadline_at - context.limits.request_deadline
    return asyncio.get_running_loop().time() - started


def _public_ok(state: dict[str, Any]) -> bool | None:
    route = state.get("route")
    return route.public_ok if route is not None else None

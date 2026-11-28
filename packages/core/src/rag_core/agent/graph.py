"""The bounded graph (ADR 0006, LLD "6.3 Agent run"): guard → route → cache → retrieve → rerank → assess →
optional reformulate → answer → verify → optional repair, with abstain, decline and search-only exits.

`run_agent` emits the whole stream after the gate, ending with `done`; it never raises.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from rag_core.agent import nodes
from rag_core.agent.context import AgentContext, answer_language
from rag_core.agent.state import AgentState
from rag_core.events import NO_USAGE_OUTCOMES, Claim, Done, Outcome, Usage
from rag_core.services.answer_cache import end_with_hit
from rag_core.services.stream import emit_degraded, end_with_error
from rag_core.units import MS_PER_S

log = structlog.get_logger()

Agent = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    public_ok: bool | None  # None when `route` didn't answer
    intent: str | None  # what `route` decided, for the stored trace; None when it didn't answer
    language: str | None
    cached: bool = False  # the answer came from the answer cache


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


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Agent:
    """The graph; with a checkpointer, each run reads and stores its session's conversation (LLD "6.3 Agent
    run", conversation)."""
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("guard", nodes.guard_step)
    graph.add_node("route", nodes.route_step)
    graph.add_node("cache", nodes.cache_step)
    graph.add_node("retrieve", nodes.retrieve_step)
    graph.add_node("assess", nodes.assess_step)
    graph.add_node("reformulate", nodes.reformulate_step)
    graph.add_node("answer", nodes.answer_step)
    graph.add_node("verify", nodes.verify_step)
    graph.add_node("repair", nodes.repair_step)
    graph.add_node("search_only", nodes.search_only_step)
    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", _to("route"), ["route", "search_only", END])
    graph.add_conditional_edges("route", _to("cache"), ["cache", "search_only", END])
    graph.add_conditional_edges("cache", _to("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("retrieve", _after_retrieve, ["assess", "answer", "search_only", END])
    graph.add_conditional_edges("assess", _after_assess, ["reformulate", "answer", "search_only", END])
    graph.add_conditional_edges("reformulate", _to("retrieve"), ["retrieve", "search_only", END])
    graph.add_conditional_edges("answer", _to("verify"), ["verify", "search_only", END])
    graph.add_conditional_edges("verify", _after_verify, ["repair", "search_only", END])
    graph.add_conditional_edges("repair", _to("verify"), ["verify", "search_only", END])
    graph.add_edge("search_only", END)
    return graph.compile(checkpointer=checkpointer)


async def run_agent(
    question: str, session: UUID, trace_id: UUID, *, agent: Agent, context: AgentContext
) -> RunResult:
    """Runs the graph for one question of the session and ends the stream with `usage` (unless search-only)
    and `done`. With a checkpointer, the session's thread is read before the run and written once at its
    end, even when a step raises (a run cut by the deadline keeps what it wrote)."""
    state: dict[str, Any] = {}
    deadline = asyncio.timeout_at(context.deadline_at)
    try:
        async with deadline:
            async for chunk in agent.astream(
                {"question": question},
                context=context,
                config={
                    "recursion_limit": context.limits.recursion_limit,
                    "configurable": {"thread_id": str(session)},  # the session's thread
                },
                stream_mode="values",
                durability="exit" if agent.checkpointer else None,  # one write per run
            ):
                state = chunk
        outcome: Outcome = state["outcome"]
    except GraphRecursionError:
        log.error("recursion_limit")  # a bug: the graph looped past its backstop
        outcome = "abstained"
    except TimeoutError:
        if deadline.expired():
            log.warning("deadline_exceeded", stage="agent")
            settled = _on_deadline(context)
        else:  # a step's own timeout escaped it: a bug, not the request's deadline
            log.exception("agent_failed")
            settled = context.settled
        if settled is None:
            end_with_error(context.sink, trace_id, context.contact)
            return _result("error", state)
        outcome = settled
    except Exception:
        log.exception("agent_failed")
        if context.settled is None:
            end_with_error(context.sink, trace_id, context.contact)
            return _result("error", state)
        outcome = context.settled  # the ending already sent stands
    usage = Usage(
        tokens_in=context.tokens_in,
        tokens_out=context.tokens_out,
        cost_usd=context.cost_usd,
        latency_ms=round(_elapsed(context) * MS_PER_S),
    )
    hit_claims = state.get("hit_claims")
    if state.get("cached") and hit_claims:
        end_with_hit(context.sink, hit_claims, usage, trace_id, context.contact)
        return _result(outcome, state)
    if outcome not in NO_USAGE_OUTCOMES:
        context.sink.record(usage)
    suggestion = await _suggestion(state, outcome, context)
    cached = bool(state.get("cached"))
    context.sink.record(
        Done(
            trace_id=trace_id,
            outcome=outcome,
            cached=cached,
            suggestion=suggestion,
            contact=list(context.contact),
            notice=state.get("notice"),
        )
    )
    _remember(state, outcome, context)
    return _result(outcome, state)


def _remember(state: dict[str, Any], outcome: Outcome, context: AgentContext) -> None:
    """After `done`, stores a run that ended as answered with the claims `verify` sent, in the background
    (LLD "6.9 Answer cache"). Nothing else is stored: a hit, a contact reply, an abstention, a decline, a
    block, a degraded answer, nor a run cut after its claims. The vector is there only when the lookup ran
    and missed, which is also when the question is storable."""
    vector, claims, route = state.get("query_vector"), state.get("claims"), state.get("route")
    # The graph's own ending: after a cut, `claims` may be those of an earlier pass, not the ones sent.
    if (
        outcome != "answered"
        or state.get("outcome") != "answered"
        or state.get("cached")
        or vector is None
        or not claims
        or route is None
    ):
        return
    context.cache.remember(
        route.standalone_question,
        route.language,
        context.corpus_version,
        vector,
        [Claim(i=i, text=c.text, sources=list(c.sources), verdict=c.verdict) for i, c in enumerate(claims)],
    )


async def _suggestion(state: dict[str, Any], outcome: Outcome, context: AgentContext) -> str | None:
    """The closest example question, for an abstention or a decline; None when there is none to give. It
    never fails the run: an embedder that is down leaves the stream without a suggestion."""
    route = state.get("route")
    if outcome not in ("abstained", "declined") or route is None:
        return None
    try:
        return await context.suggestions.closest(
            route.standalone_question,
            answer_language(route.language, context.languages),
            context.deadline_at,
            vector=context.vectors.get(route.standalone_question),
        )
    except Exception as exc:
        log.warning("suggestion_failed", error=type(exc).__name__)  # the type only: it may quote the text
        return None


def _on_deadline(context: AgentContext) -> Outcome | None:
    """How a run the deadline cut ends: the ending a step already sent; an abstention after a draft (never
    published unverified); the passages found, search-only; or None, an error, when nothing was found in
    time ("no matching sources" would be untrue)."""
    if context.settled is not None:
        return context.settled
    if context.drafted:
        return "abstained"
    if not context.found:
        return None
    emit_degraded(
        context.sink, context.found, context.corpus_version, "deadline", context.limits.degraded_max_passages
    )
    return "degraded"


def _elapsed(context: AgentContext) -> float:
    """Seconds since the request started: its deadline is `REQUEST_DEADLINE` after the start."""
    started = context.deadline_at - context.limits.request_deadline
    return asyncio.get_running_loop().time() - started


def _result(outcome: Outcome, state: dict[str, Any]) -> RunResult:
    route = state.get("route")
    if route is None:
        return RunResult(outcome, None, None, None)
    return RunResult(outcome, route.public_ok, route.intent, route.language, bool(state.get("cached")))

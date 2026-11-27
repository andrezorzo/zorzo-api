"""One function per step of the graph (LLD "6.3 Agent run", "6.4 Answer contract and verification").

A step returns the state it changes. It never raises for a model failure: it sets `degraded` (search-only,
before the draft) or `outcome` (the run ends), and the graph's edges read them.
"""

from collections.abc import Sequence
from typing import Any, cast

from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict

from rag_core.agent.context import AgentContext
from rag_core.agent.state import AgentState
from rag_core.events import Claim, Token
from rag_core.services import retrieval
from rag_core.services.gateway import Completion, ModelsUnavailableError
from rag_core.services.prompts import fit_passages, messages, passages_text
from rag_core.services.route import route
from rag_core.services.stream import emit_degraded, emit_retrieval, stage
from rag_core.services.verification import (
    AnswerContract,
    CheckedClaim,
    Verdicts,
    apply_verdicts,
    as_data,
    cite,
    coherent,
    draft_text,
    judge_input,
    read_answer,
)

# The document kinds an intent's retrieval reads; the rest read every kind (LLD "6.3 Agent run": career
# facts come from the FAQ only).
KINDS_BY_INTENT = {"career_fact": ("faq",)}
# While the draft streams, its JSON is parsed again once this many characters arrived (or a string closed).
DRAFT_PARSE_EVERY = 24

AgentRuntime = Runtime[AgentContext]


class AssessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sufficient: bool


class ReformulateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str


def _language(state: AgentState, ctx: AgentContext) -> str:
    language = state["route"].language
    return language if language in ctx.languages else ctx.languages[0]


def _answer_messages(state: AgentState, ctx: AgentContext) -> list[dict[str, str]]:
    return messages(
        ctx.prompts.answer,
        state["query"],
        language=_language(state, ctx),
        passages=passages_text(state["passages"]),
    )


async def route_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    with stage(ctx.sink, "route"):
        try:
            result, completion = await route(
                state["question"],
                gateway=ctx.gateway,
                prompt=ctx.prompts.route,
                deadline_at=ctx.before_answer,
                spend=ctx.spend,
            )
        except ModelsUnavailableError:
            return {"degraded": ctx.unavailable()}
        ctx.record("route", completion)
    if result.intent == "off_topic":
        return {"route": result, "outcome": "declined"}
    return {"route": result, "query": result.standalone_question}


async def retrieve_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    ranked = await retrieval.retrieve(
        state["query"],
        ctx.corpus_version,
        ctx.sink,
        ctx.embedder,
        ctx.store,
        ctx.rerankers,
        ctx.limits,
        KINDS_BY_INTENT.get(state["route"].intent),
    )
    # A rewritten query that finds nothing keeps the passages the first one found.
    if not ranked and state.get("passages"):
        return {}
    emit_retrieval(ctx.sink, ranked, ctx.corpus_version)
    ctx.found = ranked
    passages = fit_passages(ranked, ctx.limits.context_max_tokens)
    floor, skip = ctx.limits.relevance_floor, ctx.limits.assess_skip_score
    if not passages or (floor is not None and passages[0].score < floor):
        return {"passages": passages, "outcome": "abstained"}
    return {"passages": passages, "skip_assess": skip is not None and passages[0].score >= skip}


async def assess_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    prompt = messages(ctx.prompts.assess, state["query"], passages=passages_text(state["passages"]))
    with stage(ctx.sink, "assess"):
        result = await ctx.call("assess", "route", prompt, AssessResult, ctx.before_answer)
    if result is None:
        return {"degraded": ctx.unavailable()}
    weak = not cast(AssessResult, result).sufficient
    return {"reformulate": weak and state.get("reformulated", 0) < ctx.limits.max_reformulate}


async def reformulate_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    prompt = messages(ctx.prompts.reformulate, state["query"])
    with stage(ctx.sink, "reformulate"):
        result = await ctx.call("reformulate", "route", prompt, ReformulateResult, ctx.before_answer)
    if result is None:
        return {"degraded": ctx.unavailable()}
    query = cast(ReformulateResult, result).query.strip() or state["query"]
    return {"query": query, "reformulated": state.get("reformulated", 0) + 1}


def _no_answer(ctx: AgentContext) -> dict[str, Any]:
    """The answer stage failed: a draft is never published unverified; with none sent yet, the passages
    still help."""
    return {"outcome": "abstained"} if ctx.drafted else {"degraded": "models_unavailable"}


async def answer_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    if ctx.seconds_left() < ctx.limits.answer_reserve:
        return {"degraded": "deadline"}
    prompt = _answer_messages(state, ctx)
    with stage(ctx.sink, "answer"):
        try:
            answer = read_answer((await _stream_draft(prompt, ctx)).text)
        except ModelsUnavailableError:
            return _no_answer(ctx)
        if answer is None:  # asked again with the contract as output schema; no more tokens
            answer = cast(AnswerContract | None, await ctx.call("answer", "answer", prompt, AnswerContract))
            if answer is None:
                return _no_answer(ctx)
    if answer.abstained or not answer.claims:
        return {"answer": answer, "outcome": "abstained"}
    return {"answer": answer}


async def _stream_draft(prompt: list[dict[str, str]], ctx: AgentContext) -> Completion:
    """Streams the answer, sending as `token` only the new characters of the claims' text."""
    text, sent, parsed_at = "", "", 0
    async for part in ctx.gateway.stream("answer", prompt, deadline_at=ctx.deadline_at, spend=ctx.spend):
        if isinstance(part, Completion):
            _send_draft(draft_text(text), sent, ctx)  # what arrived since the last parse
            ctx.record("answer", part)
            return part
        text += part
        # Parsing the whole JSON so far on every chunk is quadratic: parse when enough arrived.
        if len(text) - parsed_at >= DRAFT_PARSE_EVERY or '"' in part:
            parsed_at = len(text)
            sent = _send_draft(draft_text(text), sent, ctx)
    raise ModelsUnavailableError("answer")  # the gateway always ends with its completion or raises


def _send_draft(draft: str, sent: str, ctx: AgentContext) -> str:
    """Sends what the draft gained since `sent`; returns the draft sent so far."""
    if not draft.startswith(sent) or len(draft) == len(sent):
        return sent
    ctx.sink.record(Token(text=draft[len(sent) :]))
    ctx.drafted = True
    return draft


async def verify_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    answer = state["answer"]
    with stage(ctx.sink, "verify"):
        cited = cite(answer, state["passages"])
        verdicts: Verdicts | None = None
        if any(c.passages for c in cited):  # claims without a valid citation get no vote
            prompt = messages(ctx.prompts.judge, judge_input(cited))
            verdicts = cast(Verdicts | None, await ctx.call("verify", "judge", prompt, Verdicts))
            if verdicts is None:
                return {"outcome": "abstained"}  # verification is never skipped
        checked = apply_verdicts(cited, verdicts, answer.gap)
    if coherent(checked):
        _send_claims(checked, ctx)
        return {"claims": checked, "outcome": "answered"}
    if state.get("repairs", 0) < ctx.limits.max_repair:
        return {"claims": checked}
    return {"claims": checked, "outcome": "abstained"}


def _send_claims(claims: Sequence[CheckedClaim], ctx: AgentContext) -> None:
    for i, claim in enumerate(claims):
        ctx.sink.record(Claim(i=i, text=claim.text, sources=list(claim.sources), verdict=claim.verdict))
    ctx.settled = "answered"  # only once the whole ending is out


async def repair_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    checked = "\n".join(
        f'<claim verdict="{c.verdict}">{as_data(c.text)}</claim>'
        for c in state["claims"]
        if c.verdict != "gap"
    )
    answer_prompt = _answer_messages(state, ctx)[0]["content"]
    user = f"{as_data(state['query'])}\n\n<checked_claims>\n{checked}\n</checked_claims>"
    prompt = messages(ctx.prompts.repair, user, answer_prompt=answer_prompt)
    with stage(ctx.sink, "repair"):
        answer = cast(AnswerContract | None, await ctx.call("repair", "answer", prompt, AnswerContract))
    if answer is None:
        return {"outcome": "abstained"}
    repairs = state.get("repairs", 0) + 1
    if answer.abstained or not answer.claims:
        return {"answer": answer, "repairs": repairs, "outcome": "abstained"}
    return {"answer": answer, "repairs": repairs}


async def search_only_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    """The passages already found, or a retrieval of the question, as a search-only answer."""
    ctx = runtime.context
    hits = ctx.found or await retrieval.retrieve(
        state["question"], ctx.corpus_version, ctx.sink, ctx.embedder, ctx.store, ctx.rerankers, ctx.limits
    )
    emit_degraded(ctx.sink, hits, ctx.corpus_version, state["degraded"], ctx.limits.degraded_max_passages)
    ctx.settled = "degraded"
    return {"outcome": "degraded"}

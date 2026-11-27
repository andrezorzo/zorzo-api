"""One function per step of the graph (LLD "6.3 Agent run", "6.4 Answer contract and verification").

A step returns the state it changes. It never raises for a model failure: it sets `degraded` (search-only,
before the draft) or `outcome` (the run ends), and the graph's edges read them.
"""

from collections.abc import Sequence
from string import Template
from typing import Any, cast

from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict

from rag_core.agent.context import AgentContext
from rag_core.agent.state import AgentState
from rag_core.events import Claim, Hit, Retrieval, Token
from rag_core.ports import SearchHit
from rag_core.services import search_only
from rag_core.services.gateway import CHARS_PER_TOKEN, Completion, ModelsUnavailableError
from rag_core.services.route import route
from rag_core.services.search_only import stage
from rag_core.services.verification import (
    AnswerContract,
    CheckedClaim,
    Verdicts,
    apply_verdicts,
    cite,
    coherent,
    draft_text,
    judge_input,
    needs_judge,
    read_answer,
)

# Retrieval reads only the FAQ for these intents (LLD "6.3 Agent run", career facts).
FAQ_ONLY = {"career_fact": ("faq",)}

_STRICT = ConfigDict(extra="forbid", frozen=True)


class AssessResult(BaseModel):
    model_config = _STRICT
    sufficient: bool


class ReformulateResult(BaseModel):
    model_config = _STRICT
    query: str


Context = Runtime[AgentContext]


def _system(prompt: str, question: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": prompt}, {"role": "user", "content": question}]


def _passages_text(passages: Sequence[SearchHit]) -> str:
    """The passages, numbered from 1 and marked as data."""
    return "\n".join(
        f'<passage n="{n}" title="{hit.title}">\n{hit.content}\n</passage>'
        for n, hit in enumerate(passages, 1)
    )


def _fit(hits: Sequence[SearchHit], max_tokens: int) -> list[SearchHit]:
    """Whole passages in rank order while they fit the prompt's budget; the lowest-ranked go first."""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits:
        tokens = -(-len(hit.content) // CHARS_PER_TOKEN)
        if used + tokens > max_tokens:
            break
        kept.append(hit)
        used += tokens
    return kept


def _language(state: AgentState, ctx: AgentContext) -> str:
    language = state["route"].language
    return language if language in ctx.languages else ctx.languages[0]


async def route_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    with stage(ctx.sink, "route"):
        try:
            result, completion = await route(
                state["question"],
                gateway=ctx.gateway,
                prompt=ctx.prompts.route,
                deadline_at=ctx.deadline_at,
                spend=ctx.spend,
            )
        except ModelsUnavailableError:
            return {"degraded": "models_unavailable"}
        ctx.count("route", completion)
    if result.intent == "off_topic":
        return {"route": result, "outcome": "declined"}
    return {"route": result, "query": result.standalone_question}


async def retrieve_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    kinds = FAQ_ONLY.get(state["route"].intent)
    ranked = await search_only.retrieve(
        state["query"],
        ctx.corpus_version,
        ctx.sink,
        ctx.embedder,
        ctx.store,
        ctx.rerankers,
        ctx.limits,
        kinds,
    )
    if not ranked and state.get("passages"):  # a reformulated query that found nothing keeps the first
        return {}
    ctx.sink.record(
        Retrieval(
            hits=[Hit(id=h.id, title=h.title, score=h.score) for h in ranked],
            corpus_version=ctx.corpus_version,
        )
    )
    ctx.passages = ranked
    passages = _fit(ranked, ctx.limits.context_max_tokens)
    if not passages:
        return {"passages": [], "outcome": "abstained"}
    floor = ctx.limits.relevance_floor
    if floor is not None and passages[0].score < floor:
        return {"passages": passages, "outcome": "abstained"}
    skip = ctx.limits.assess_skip_score
    return {"passages": passages, "skip_assess": skip is not None and passages[0].score >= skip}


async def assess_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    prompt = Template(ctx.prompts.assess).safe_substitute(
        question=state["query"], passages=_passages_text(state["passages"])
    )
    with stage(ctx.sink, "assess"):
        try:
            completion = await ctx.gateway.complete(
                "route", _system(prompt, state["query"]), schema=AssessResult, deadline_at=ctx.deadline_at,
                spend=ctx.spend,
            )  # fmt: skip
        except ModelsUnavailableError:
            return {"degraded": "models_unavailable"}
        ctx.count("assess", completion)
    weak = not cast(AssessResult, completion.parsed).sufficient
    return {"reformulate": weak and state.get("reformulated", 0) < ctx.limits.max_reformulate}


async def reformulate_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    prompt = Template(ctx.prompts.reformulate).safe_substitute(question=state["query"])
    with stage(ctx.sink, "reformulate"):
        try:
            completion = await ctx.gateway.complete(
                "route", _system(prompt, state["query"]), schema=ReformulateResult,
                deadline_at=ctx.deadline_at, spend=ctx.spend,
            )  # fmt: skip
        except ModelsUnavailableError:
            return {"degraded": "models_unavailable"}
        ctx.count("reformulate", completion)
    query = cast(ReformulateResult, completion.parsed).query.strip() or state["query"]
    return {"query": query, "reformulated": state.get("reformulated", 0) + 1}


def _answer_prompt(state: AgentState, ctx: AgentContext) -> str:
    return Template(ctx.prompts.answer).safe_substitute(
        language=_language(state, ctx), passages=_passages_text(state["passages"])
    )


async def answer_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    if ctx.left() < ctx.limits.answer_reserve:
        return {"degraded": "deadline"}
    messages = _system(_answer_prompt(state, ctx), state["query"])
    with stage(ctx.sink, "answer"):
        try:
            completion = await _stream_draft(messages, ctx)
        except ModelsUnavailableError:
            # A draft is never published unverified; with none sent yet, the passages still help.
            return {"outcome": "abstained"} if ctx.drafted else {"degraded": "models_unavailable"}
        answer = read_answer(completion.text)
        if answer is None:  # asked again with the contract as output schema; no more tokens
            try:
                completion = await ctx.gateway.complete(
                    "answer", messages, schema=AnswerContract, deadline_at=ctx.deadline_at, spend=ctx.spend
                )
            except ModelsUnavailableError:
                return {"outcome": "abstained"} if ctx.drafted else {"degraded": "models_unavailable"}
            ctx.count("answer", completion)
            answer = cast(AnswerContract, completion.parsed)
    if answer.abstained or not answer.claims:
        return {"answer": answer, "outcome": "abstained"}
    return {"answer": answer}


async def _stream_draft(messages: list[dict[str, str]], ctx: AgentContext) -> Completion:
    """Streams the answer, sending as `token` only the new characters of the claims' text."""
    text, sent = "", ""
    async for part in ctx.gateway.stream("answer", messages, deadline_at=ctx.deadline_at, spend=ctx.spend):
        if isinstance(part, Completion):
            ctx.count("answer", part)
            return part
        text += part
        draft = draft_text(text)
        if draft.startswith(sent) and len(draft) > len(sent):
            ctx.sink.record(Token(text=draft[len(sent) :]))
            ctx.drafted = True
            sent = draft
    raise ModelsUnavailableError("answer")  # the gateway always ends with its completion or raises


async def verify_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    answer = state["answer"]
    with stage(ctx.sink, "verify"):
        cited = cite(answer, state["passages"])
        verdicts = Verdicts(verdicts=[])
        if needs_judge(cited):
            prompt = Template(ctx.prompts.judge).safe_substitute(claims=judge_input(cited))
            try:
                completion = await ctx.gateway.complete(
                    "judge", _system(prompt, state["query"]), schema=Verdicts, deadline_at=ctx.deadline_at,
                    spend=ctx.spend,
                )  # fmt: skip
            except ModelsUnavailableError:
                return {"outcome": "abstained"}  # verification is never skipped
            ctx.count("verify", completion)
            verdicts = cast(Verdicts, completion.parsed)
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


async def repair_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    ctx = runtime.context
    verdicts = "\n".join(f"- {c.text} ({c.verdict})" for c in state["claims"] if c.verdict != "gap")
    prompt = Template(ctx.prompts.repair).safe_substitute(
        answer_prompt=_answer_prompt(state, ctx), verdicts=verdicts
    )
    with stage(ctx.sink, "repair"):
        try:
            completion = await ctx.gateway.complete(
                "answer", _system(prompt, state["query"]), schema=AnswerContract, deadline_at=ctx.deadline_at,
                spend=ctx.spend,
            )  # fmt: skip
        except ModelsUnavailableError:
            return {"outcome": "abstained"}
        ctx.count("repair", completion)
    answer = cast(AnswerContract, completion.parsed)
    repairs = state.get("repairs", 0) + 1
    if answer.abstained or not answer.claims:
        return {"answer": answer, "repairs": repairs, "outcome": "abstained"}
    return {"answer": answer, "repairs": repairs}


async def search_only_step(state: AgentState, runtime: Context) -> dict[str, Any]:
    """The passages already found, or a retrieval of the question, as a search-only answer."""
    ctx = runtime.context
    hits = ctx.passages
    if not hits:
        hits = await search_only.retrieve(
            state["question"],
            ctx.corpus_version,
            ctx.sink,
            ctx.embedder,
            ctx.store,
            ctx.rerankers,
            ctx.limits,
        )
    search_only.emit_degraded(ctx.sink, hits, ctx.corpus_version, state["degraded"], ctx.limits)
    return {"outcome": "degraded"}

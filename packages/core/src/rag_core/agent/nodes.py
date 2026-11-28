"""One function per step of the graph (LLD "6.3 Agent run", "6.4 Answer contract and verification").

A step returns the state it changes. It never raises for a model failure: it sets `degraded` (search-only,
before the draft) or `outcome` (the run ends), and the graph's edges read them.
"""

import asyncio
import json
from collections.abc import Sequence
from contextlib import aclosing
from typing import Any, Literal, cast

import structlog
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, ValidationError

from rag_core.agent.context import AgentContext, answer_language
from rag_core.agent.state import AgentState
from rag_core.events import BlockCategory, Blocked, Claim, Guard, GuardLayer, Token
from rag_core.services import retrieval
from rag_core.services.conversation import Conversation, begin_turn, live, with_claims
from rag_core.services.gateway import Completion, ModelsUnavailableError
from rag_core.services.guard import CanaryScan, check_input
from rag_core.services.prompts import (
    answer_messages,
    fit_passages,
    messages,
    passages_text,
    repair_messages,
)
from rag_core.services.route import route
from rag_core.services.stream import emit_degraded, emit_retrieval, stage
from rag_core.services.verification import (
    AnswerContract,
    CheckedClaim,
    CitedClaim,
    Verdicts,
    apply_verdicts,
    cite,
    coherent,
    draft_text,
    enforce_links,
    judge_input,
    read_answer,
)

# While the draft streams, its JSON is parsed again once this many characters arrived (or a string closed).
DRAFT_PARSE_EVERY = 24

AgentRuntime = Runtime[AgentContext]

log = structlog.get_logger()

INPUT_CHECK_LABEL = "Injection classifier"
INJECTION_MESSAGE = "The question looks like an attempt to steer the assistant."
LEAK_MESSAGE = "The answer holds the assistant's internal marker."
LEAKED: Literal["leaked"] = "leaked"  # what `_answer_contract` returns for a draft that held the canary


class _CanaryLeakError(Exception):
    """The draft holds the canary: the answer is stopped and the run is blocked (`L5`)."""


# The verdicts whose text a remembered turn keeps: what the visitor was shown as checked.
REMEMBERED_VERDICTS = frozenset({"supported", "partial"})


class AssessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sufficient: bool


class ReformulateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: str


def _language(state: AgentState, ctx: AgentContext) -> str:
    return answer_language(state["route"].language, ctx.languages)


def _answer_messages(state: AgentState, ctx: AgentContext) -> list[dict[str, str]]:
    return answer_messages(
        ctx.prompts.answer,
        state["query"],
        language=_language(state, ctx),
        passages=passages_text(state["passages"]),
        limits=ctx.limits,
    )


def _block(ctx: AgentContext, layer: GuardLayer, category: BlockCategory, message: str) -> dict[str, Any]:
    """Counts the block, sends `blocked` and ends the run. Call it after the step's `stage` ended: only
    `done` may follow."""
    ctx.settled = "blocked"  # first: a failure after `blocked` must not add `error` to the stream
    ctx.defenses.record(layer, category, "block", ctx.trace_id)
    ctx.sink.record(Blocked(layer=layer, category=category, message=message))
    return {"outcome": "blocked"}


async def guard_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    """Layer L2: the local classifier reads the question before any model call. Under the deadline that keeps
    `ANSWER_RESERVE`, like every step before `answer`; cut there, the run goes search-only."""
    ctx = runtime.context
    with stage(ctx.sink, "guard"):
        try:
            async with asyncio.timeout_at(ctx.before_answer):
                action = await check_input(ctx.classifier, state["question"], ctx.limits.guard_threshold)
        except TimeoutError:
            return {"degraded": ctx.unavailable()}
        ctx.sink.record(Guard(layer="L2", label=INPUT_CHECK_LABEL, action=action))
    if action == "block":
        return _block(ctx, "L2", "injection", INJECTION_MESSAGE)
    return {}


def _conversation(state: AgentState) -> Conversation:
    """The stored conversation; one that no longer reads as a conversation is dropped, so the session starts
    over instead of failing on every question."""
    try:
        return Conversation.model_validate(state.get("conversation") or {})
    except ValidationError:
        log.warning("conversation_unreadable")
        return Conversation()


async def route_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    """Routes the question with the live conversation. A follow-up with no history abstains here
    (`no_context`) and is not remembered; any other routed question becomes the conversation's new turn."""
    ctx = runtime.context
    conversation = live(_conversation(state), ctx.now, ctx.limits.session_ttl)
    with stage(ctx.sink, "route"):
        try:
            result, completion = await route(
                state["question"],
                conversation,
                limits=ctx.limits,
                gateway=ctx.gateway,
                prompt=ctx.prompts.route,
                schema=ctx.route_schema,
                deadline_at=ctx.before_answer,
                spend=ctx.spend,
            )
        except ModelsUnavailableError:
            return {"degraded": ctx.unavailable()}
        ctx.record("route", completion)
    if result.follow_up and not conversation.turns:
        return {"route": result, "outcome": "abstained", "notice": "no_context"}
    turn = begin_turn(conversation, ctx.redactor.text(result.standalone_question), ctx.now, ctx.limits)
    update: dict[str, Any] = {"route": result, "conversation": turn.model_dump()}
    if result.language not in ctx.languages:  # answered in the default language, with a line saying so
        update["notice"] = "other_language"
    action = ctx.intents[result.intent].action
    if action == "decline":
        return {**update, "outcome": "declined"}
    if action == "contact":  # the reply is the contact options `done` carries
        return {**update, "outcome": "answered"}
    return {**update, "query": result.standalone_question}


async def cache_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    """Looks the standalone question up in the answer cache (LLD "6.9 Answer cache"). A hit replays the
    stored claims and ends the run as answered; a miss keeps the question's vector for the row the run may
    store. Skipped, with no `stage`, for a language the domain doesn't declare or a question with personal
    data. A failing lookup is a miss."""
    ctx = runtime.context
    route = state["route"]
    if ctx.cache.skips(route.standalone_question, route.language):
        return {}
    with stage(ctx.sink, "cache"):
        found = await ctx.cache.lookup(
            route.standalone_question, route.language, ctx.corpus_version, ctx.before_answer
        )
    if found.claims is None:
        if found.vector is None:
            return {}
        ctx.vectors[route.standalone_question] = found.vector  # retrieval and the suggestion reuse it
        return {"query_vector": found.vector}
    ctx.settled = "answered"  # first: a failure halfway must not add `error` after claims the visitor has
    ctx.cache.replay(ctx.sink, found.claims)
    return {"cached": True, "outcome": "answered"}


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
        ctx.intents[state["route"].intent].kinds or None,  # the intent's document kinds, or all
        vectors=ctx.vectors,  # kept for the suggestion of an abstention
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
            answer = await _answer_contract(prompt, ctx)
        except ModelsUnavailableError:
            return _no_answer(ctx)
    if isinstance(answer, str):  # `LEAKED`
        return _block(ctx, "L5", "prompt_leak", LEAK_MESSAGE)  # after the stage ended: only `done` follows
    if answer is None:
        return _no_answer(ctx)
    if answer.abstained or not answer.claims:
        return {"answer": answer, "outcome": "abstained"}
    return {"answer": answer}


async def _answer_contract(
    prompt: list[dict[str, str]], ctx: AgentContext
) -> AnswerContract | Literal["leaked"] | None:
    """The streamed answer; when it isn't the contract, asked again with the contract as output schema (no
    more tokens). None when the chain can't answer, `LEAKED` when the draft held the canary."""
    try:
        answer = read_answer((await _stream_draft(prompt, ctx)).text)
    except _CanaryLeakError:
        return LEAKED
    if answer is None:
        answer = cast(AnswerContract | None, await ctx.call("answer", "answer", prompt, AnswerContract))
    return answer


async def _stream_draft(prompt: list[dict[str, str]], ctx: AgentContext) -> Completion:
    """Streams the answer, sending as `token` only the new characters of the claims' text, less the last ones
    that could begin the canary. Raises `_CanaryLeakError` when the draft holds it."""
    scan = ctx.canary.scan()
    text, sent, parsed_at = "", "", 0
    # aclosing: a leak stops reading the stream, which the gateway settles as for a visitor who left.
    async with aclosing(
        ctx.gateway.stream("answer", prompt, deadline_at=ctx.deadline_at, spend=ctx.spend)
    ) as parts:
        async for part in parts:
            if isinstance(part, Completion):
                ctx.record("answer", part)  # first: a leak in the last chunk still cost what it cost
                _release(text, sent, scan, ctx, final=True)
                return part
            text += part
            # Parsing the whole JSON so far on every chunk is quadratic: parse when enough arrived.
            if len(text) - parsed_at >= DRAFT_PARSE_EVERY or '"' in part:
                parsed_at = len(text)
                sent = _release(text, sent, scan, ctx, final=False)
    raise ModelsUnavailableError("answer")  # the gateway always ends with its completion or raises


def _release(text: str, sent: str, scan: CanaryScan, ctx: AgentContext, *, final: bool) -> str:
    """Checks the draft read from `text` for the canary (raising `_CanaryLeakError`) and sends what it gained;
    unless `final`, less the last characters that could begin the canary. Returns the draft sent so far."""
    draft = draft_text(text)
    if scan.hit(draft):
        raise _CanaryLeakError
    return _send_draft(draft if final else ctx.canary.safe(draft), sent, ctx)


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
        cited = cite(answer, state["passages"], ctx.link_hosts)
        verdicts: Verdicts | None = None
        if any(c.passages for c in cited):  # claims without a valid citation get no vote
            prompt = messages(ctx.prompts.judge, judge_input(cited))
            verdicts = cast(Verdicts | None, await ctx.call("verify", "judge", prompt, Verdicts))
            if verdicts is None:
                return {"outcome": "abstained"}  # verification is never skipped
        verdicted = apply_verdicts(cited, verdicts, answer.gap)
    return publish_claims(verdicted, cited, state.get("repairs", 0), _conversation(state), ctx)


def publish_claims(
    verdicted: Sequence[CheckedClaim],
    cited: Sequence[CitedClaim],
    repairs: int,
    conversation: Conversation,
    ctx: AgentContext,
) -> dict[str, Any]:
    """The one way claims reach the visitor: held to the link allowlist, then to the canary check (the
    texts joined as the draft's are, so a canary split across two claims is found), then sent, and their
    text remembered as the conversation's last turn; a canary blocks the run (`L5`). Without a claim left to
    publish, a repair, or the abstention."""
    checked = enforce_links(verdicted, cited, ctx.link_hosts)
    if coherent(checked):
        # What goes out is checked last: the judge's `supported_text` and the gap are model text too.
        if ctx.canary.found(" ".join(c.text for c in checked if c.text)):
            return _block(ctx, "L5", "prompt_leak", LEAK_MESSAGE)
        _send_claims(checked, ctx)
        shown = [ctx.redactor.text(c.text) for c in checked if c.verdict in REMEMBERED_VERDICTS]
        remembered = with_claims(conversation, shown).model_dump()
        return {"claims": checked, "outcome": "answered", "conversation": remembered}
    if repairs < ctx.limits.max_repair:
        return {"claims": checked}
    return {"claims": checked, "outcome": "abstained"}


def _send_claims(claims: Sequence[CheckedClaim], ctx: AgentContext) -> None:
    # Settled first: a failure halfway must not add `error` after claims the visitor already has.
    ctx.settled = "answered"
    for i, claim in enumerate(claims):
        ctx.sink.record(Claim(i=i, text=claim.text, sources=list(claim.sources), verdict=claim.verdict))


async def repair_step(state: AgentState, runtime: AgentRuntime) -> dict[str, Any]:
    ctx = runtime.context
    # The question and the claims are text the visitor may have steered: as JSON strings they stay data.
    checked = [{"claim": c.text, "verdict": c.verdict} for c in state["claims"] if c.verdict != "gap"]
    user = json.dumps({"question": state["query"], "checked_claims": checked}, ensure_ascii=False)
    prompt = repair_messages(
        ctx.prompts.repair,
        ctx.prompts.answer,
        user,
        language=_language(state, ctx),
        passages=passages_text(state["passages"]),
        limits=ctx.limits,
    )
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
    ctx.settled = "degraded"  # first: only passages and `done` may follow `degraded`, never `error`
    emit_degraded(ctx.sink, hits, ctx.corpus_version, state["degraded"], ctx.limits.degraded_max_passages)
    return {"outcome": "degraded"}

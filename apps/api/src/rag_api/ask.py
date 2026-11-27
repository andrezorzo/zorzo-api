"""`POST /api/v1/ask` (LLD "HTTP routes"): checks run before the stream opens, then the stream events."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from rag_core.agent.context import AgentContext
from rag_core.agent.graph import run_agent
from rag_core.errors import ApiError, ErrorCode
from rag_core.events import Done, StreamEvent
from rag_core.ports import StoreUnavailableError
from rag_core.services import corpus as corpus_service
from rag_core.services.client_ip import VIEWER_HEADER
from rag_core.services.gate import Asker
from rag_core.services.gateway import RequestSpend
from rag_core.services.search_only import end_with_error, run_search_only
from rag_core.services.trace import FanOutSink
from sse_starlette import EventSourceResponse, ServerSentEvent

log = structlog.get_logger()
router = APIRouter()

TURNSTILE_TOKEN_MAX_CHARS = 2048  # Cloudflare's maximum token length


class AskRequest(BaseModel):
    q: str  # bounded by the body cap; max_question_chars applies after trimming
    session: UUID
    turnstile: str = Field(min_length=1, max_length=TURNSTILE_TOKEN_MAX_CHARS)


class QueueSink:
    """Feeds the stream: `record` never blocks."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

    def record(self, event: StreamEvent) -> None:
        self.queue.put_nowait(event)


@router.post("/api/v1/ask")
async def ask(body: AskRequest, request: Request) -> EventSourceResponse:
    state = request.app.state
    peer = request.client.host if request.client else None
    asker = Asker(body.session, request.headers.get(VIEWER_HEADER), peer)
    # One deadline for the whole request: the gate, the corpus check and the stream share it.
    loop = asyncio.get_running_loop()
    deadline_at = loop.time() + state.limits.request_deadline
    question = await state.gate.check(body.q, body.turnstile, asker, deadline_at)
    if deadline_at - loop.time() <= 0:  # the gate finished on the deadline: nothing left for the answer
        log.warning("deadline_exceeded", stage="after_gate")
        raise ApiError(ErrorCode.UNAVAILABLE)
    # Whether the models can answer now (LLD "6.2 Daily budget", "6.6 Circuit breaker", "6.8 Free quota").
    try:
        async with asyncio.timeout_at(deadline_at):
            reason = await state.gateway.mode()
    except StoreUnavailableError as exc:
        log.error("mode_check_failed", error=type(exc).__name__)
        raise ApiError(ErrorCode.UNAVAILABLE) from exc
    except TimeoutError as exc:
        log.warning("deadline_exceeded", stage="mode")
        raise ApiError(ErrorCode.UNAVAILABLE) from exc
    # In a degraded mode the question makes no model call: the rerank chain is the local level only.
    rerankers = state.rerankers if reason is None else state.local_rerankers
    left = deadline_at - loop.time()
    if left <= 0:
        log.warning("deadline_exceeded", stage="mode")
        raise ApiError(ErrorCode.UNAVAILABLE)
    corpus = await corpus_service.servable(state.vector_store, state.embedder.tag, left)
    trace_id: str = request.state.trace_id
    stream = QueueSink()
    sink = FanOutSink([stream, *state.trace_sinks])

    async def run() -> None:
        # The stream outlives the middleware's log context, so the trace id is bound again here.
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            try:
                if reason is not None:  # a degraded mode: search-only, with no model call
                    await run_search_only(
                        question,
                        corpus,
                        UUID(trace_id),
                        sink=sink,
                        embedder=state.embedder,
                        store=state.vector_store,
                        rerankers=rerankers,
                        limits=state.limits,
                        reason=reason,
                        deadline_at=deadline_at,
                    )
                else:
                    context = AgentContext(
                        gateway=state.gateway,
                        embedder=state.embedder,
                        store=state.vector_store,
                        rerankers=rerankers,
                        limits=state.limits,
                        prompts=state.prompts,
                        languages=state.domain.languages,
                        sink=sink,
                        corpus_version=corpus.version,
                        deadline_at=deadline_at,
                        spend=RequestSpend(state.limits.request_cost_cap_usd),
                    )
                    await run_agent(question, UUID(trace_id), agent=state.agent, context=context)
            except Exception:
                # Whatever escaped the service, the visitor's stream still ends with `done`.
                log.exception("stream_failed")
                end_with_error(sink, UUID(trace_id))  # every sink, so a stored trace ends too

    async def events() -> AsyncIterator[ServerSentEvent]:
        task = asyncio.create_task(run())
        try:
            while True:
                event = await stream.queue.get()
                yield ServerSentEvent(event=event.kind, data=event.payload())
                if isinstance(event, Done):
                    break
            await task
        finally:
            task.cancel()  # the visitor left: stop the work

    return EventSourceResponse(
        events(),
        ping=state.limits.ping_interval,
        ping_message_factory=lambda: ServerSentEvent(comment="ping"),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

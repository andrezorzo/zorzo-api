"""`POST /api/v1/ask` (LLD "HTTP routes"): checks run before the stream opens, then the stream events."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from rag_core.errors import ApiError, ErrorCode
from rag_core.events import Done, StreamEvent
from rag_core.services import corpus as corpus_service
from rag_core.services.client_ip import VIEWER_HEADER
from rag_core.services.gate import Asker
from rag_core.services.search_only import end_with_error, run_search_only
from rag_core.services.trace import FanOutSink
from sse_starlette import EventSourceResponse, ServerSentEvent

log = structlog.get_logger()
router = APIRouter()


class AskRequest(BaseModel):
    q: str  # bounded by the body cap; max_question_chars applies after trimming
    session: UUID
    turnstile: str = Field(min_length=1, max_length=2048)  # Cloudflare's maximum token length


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
    left = deadline_at - loop.time()
    if left <= 0:  # the gate finished on the deadline: nothing left for the answer
        log.warning("deadline_exceeded", stage="gate")
        raise ApiError(ErrorCode.UNAVAILABLE)
    corpus = await corpus_service.servable(state.vector_store, state.embedder.tag, left)
    trace_id: str = request.state.trace_id
    stream = QueueSink()
    sink = FanOutSink([stream, *state.trace_sinks])

    async def run() -> None:
        # The stream outlives the middleware's log context, so the trace id is bound again here.
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            try:
                await run_search_only(
                    question,
                    corpus,
                    UUID(trace_id),
                    sink=sink,
                    embedder=state.embedder,
                    store=state.vector_store,
                    rerankers=state.rerankers,
                    limits=state.limits,
                    deadline_at=deadline_at,
                )
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

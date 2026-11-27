"""LangSmith (LLD "Components and dependencies", Tracing): the step events as a run tree, posted by the SDK.

A request's sink only keeps its events, with the moment each arrived; `finish`, which the route calls in the
background once the request is over, builds the run tree from them (redacted, with the original times) and
hands it to the SDK's `Client`. The `Client` puts each run on an in-memory queue, which a background thread
batches and posts with its own retries; a full queue drops the run. Nothing on the request's path waits on
LangSmith. LangChain's ambient tracing (the `LANGSMITH_TRACING` switch) is turned off by the app at startup
(`langsmith.configure(enabled=False)`): it would send the graph's state, with the raw question and the
passages, and it sees neither the LiteLLM calls nor the search-only path.

Runs carry what the step events carry, redacted: steps, models, tokens, cost, latency, sources and claims. No
prompt and no raw model output ever reaches an event, so none reaches LangSmith. Without a key, the sinks do
nothing.
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from langsmith import Client, RunTree

from rag_core.events import Claim, Done, Model, Retrieval, Stage, StreamEvent
from rag_core.ports import TraceFacts
from rag_core.services.redact import Redactor

log = structlog.get_logger()

ROOT_NAME = "ask"
# Passing `info` spares the client its lazy `GET /info`; a version is all it needs from it.
CLIENT_INFO = {"version": "0.0.0"}


class LangSmithTracer:
    """The app's one LangSmith client; without a key there is none, and every sink is a no-op."""

    def __init__(self, api_key: str | None, project: str | None, endpoint: str | None) -> None:
        self._project = project
        self._client = (
            Client(
                api_url=endpoint,
                api_key=api_key,
                info=CLIENT_INFO,
                omit_traced_runtime_info=True,  # the runtime's own metadata isn't ours to send
            )
            if api_key
            else None
        )

    def sink(self, trace_id: UUID, started_at: datetime, redactor: Redactor) -> "RunSink":
        """The sink of one request: its root run is the trace id, so a link to the trace is the trace id."""
        return RunSink(self._client, self._project, trace_id, started_at, redactor)

    def flush(self, timeout: float) -> None:
        """Wait up to `timeout` for the queue to drain (the tests read what the endpoint got)."""
        if self._client is not None:
            self._client.flush(timeout=timeout)

    def close(self, timeout: float) -> None:
        """At shutdown: wait up to `timeout` for the queue to drain, then stop the client's background
        thread and close its session; what is left is dropped."""
        if self._client is not None:
            self._client.close(timeout=timeout)


class RunSink:
    """Keeps the events of one request; `finish` posts them as runs: a child per step, `llm` runs for its
    model calls and a `retriever` run for the passages found. Its failures are logged here, never raised."""

    def __init__(
        self,
        client: Client | None,
        project: str | None,
        trace_id: UUID,
        started_at: datetime,
        redactor: Redactor,
    ) -> None:
        self._client = client
        self._project = project
        self._trace_id = trace_id
        self._started_at = started_at
        self._redactor = redactor
        self._events: list[tuple[datetime, StreamEvent]] = []

    def record(self, event: StreamEvent) -> None:
        if self._client is not None:
            self._events.append((datetime.now(UTC), event))

    def finish(self, facts: TraceFacts) -> None:
        """Post the request's run tree; `facts.question` is already redacted."""
        if self._client is None:
            return
        try:
            self._post(self._client, facts)
        except Exception as exc:
            log.warning("langsmith_failed", error=type(exc).__name__)

    def _post(self, client: Client, facts: TraceFacts) -> None:
        root = RunTree(
            id=self._trace_id,
            name=ROOT_NAME,
            run_type="chain",
            inputs={"question": facts.question},
            project_name=self._project,
            start_time=self._started_at,
            ls_client=client,
        )
        root.post()
        open_steps: dict[str, RunTree] = {}
        last = root  # the latest step's run, where the passages found belong
        claims: list[str] = []
        outcome = "error"  # a request that never reached `done`
        ended_at = datetime.now(UTC)  # a request that never reached `done` ends when the sink finishes
        for at, event in self._events:
            if isinstance(event, Stage):
                if event.status == "start":
                    if event.name in open_steps:  # started again with no end in between: the first was cut
                        self._cut(open_steps[event.name], at)
                    last = open_steps[event.name] = root.create_child(
                        name=event.name, run_type="chain", inputs={}, start_time=at
                    )
                    last.post()
                elif event.name in open_steps:
                    step = open_steps.pop(event.name)
                    step.end(outputs={"ms": event.ms}, end_time=at)
                    step.patch()
            elif isinstance(event, Model):
                self._model(open_steps.get(event.stage, last), event, at)
            elif isinstance(event, Retrieval):
                self._retrieval(last, event, at)
            elif isinstance(event, Claim):
                claims.append(event.text)
            elif isinstance(event, Done):
                outcome = event.stored_outcome
                ended_at = at
        for step in open_steps.values():  # a step cut by the deadline or a failure never sent its end
            self._cut(step, ended_at)
        root.end(
            outputs=self._redactor.walk({"outcome": outcome, "claims": claims}),
            metadata=self._redactor.walk(
                {
                    "intent": facts.intent,
                    "lang": facts.lang,
                    "corpus_version": facts.corpus_version,
                    "model_config_version": facts.model_config_version,
                }
            ),
            end_time=ended_at,
        )
        root.patch()

    def _cut(self, step: RunTree, at: datetime) -> None:
        step.end(error="cut: the step never ended", end_time=at)
        step.patch()

    def _model(self, parent: RunTree, event: Model, at: datetime) -> None:
        run = parent.create_child(
            name=event.model,
            run_type="llm",
            inputs={},
            start_time=at,
            extra={
                "metadata": {
                    "provider": event.provider,
                    "ls_provider": event.provider,
                    "ls_model_name": event.model,
                    "fallback_reason": event.fallback_reason,
                    "cost_usd": event.cost_usd,
                }
            },
        )
        run.post()
        run.end(
            outputs={
                "usage_metadata": {
                    "input_tokens": event.tokens_in,
                    "output_tokens": event.tokens_out,
                    "total_tokens": event.tokens_in + event.tokens_out,
                }
            },
            end_time=at,
        )
        run.patch()

    def _retrieval(self, parent: RunTree, event: Retrieval, at: datetime) -> None:
        documents = [
            {"page_content": hit.title, "metadata": {"id": hit.id, "score": hit.score}} for hit in event.hits
        ]
        run = parent.create_child(name="retrieval", run_type="retriever", inputs={}, start_time=at)
        run.post()
        run.end(outputs=self._redactor.walk({"documents": documents}), end_time=at)
        run.patch()

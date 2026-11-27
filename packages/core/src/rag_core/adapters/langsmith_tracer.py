"""LangSmith (LLD "Components and dependencies", Tracing): the step events as a run tree, posted by the SDK.

The SDK's `Client` puts each run on an in-memory queue, which a background thread batches and posts with its
own retries; a full queue drops the run. Nothing on the request's path waits on LangSmith. LangChain's
ambient tracing (the `LANGSMITH_TRACING` switch) stays off: it would send the graph's state, with the raw
question and the passages, and it sees neither the LiteLLM calls nor the search-only path.

Runs carry what the step events carry, redacted: steps, models, tokens, cost, latency, sources and claims. No
prompt and no raw model output ever reaches an event, so none reaches LangSmith.
"""

from uuid import UUID

import langsmith
import structlog
from langsmith import Client, RunTree

from rag_core.events import Claim, Done, Model, Retrieval, Stage, StreamEvent
from rag_core.services.redact import Redactor
from rag_core.services.trace import TraceFacts

log = structlog.get_logger()

ROOT_NAME = "ask"
# Passing `info` spares the client its lazy `GET /info`; a version is all it needs from it.
CLIENT_INFO = {"version": "0.0.0"}


def disable_ambient_tracing() -> None:
    """Turn LangChain's own tracing off for the process, whatever the environment says (see the module)."""
    langsmith.configure(enabled=False)


class LangSmithTracer:
    def __init__(self, api_key: str, project: str | None, endpoint: str | None) -> None:
        self._project = project
        self._client = Client(
            api_url=endpoint,
            api_key=api_key,
            info=CLIENT_INFO,
            omit_traced_runtime_info=True,  # the runtime's own metadata (host, versions) isn't ours to send
        )

    def sink(self, trace_id: UUID, question: str, redactor: Redactor) -> "RunSink":
        """The sink of one request: its root run is the trace id, so a link to the trace is the trace id."""
        return RunSink(self._client, self._project, trace_id, question, redactor)

    def flush(self, timeout: float) -> None:
        """At shutdown: wait up to `timeout` for the queue to drain; what is left is dropped."""
        self._client.flush(timeout=timeout)


class RunSink:
    """Maps the events of one request to runs: a child per step, `llm` runs for its model calls and a
    `retriever` run for the passages found. `finish` ends the root run once the request's facts are known."""

    def __init__(
        self, client: Client, project: str | None, trace_id: UUID, question: str, redactor: Redactor
    ) -> None:
        self._redactor = redactor
        self._root = RunTree(
            id=trace_id,
            name=ROOT_NAME,
            run_type="chain",
            inputs=redactor.walk({"question": question}),
            project_name=project,
            ls_client=client,
        )
        self._root.post()
        self._open: dict[str, RunTree] = {}
        self._last: RunTree = self._root  # the latest step's run, where the passages found belong
        self._claims: list[str] = []
        self._outcome = "error"

    def record(self, event: StreamEvent) -> None:
        if isinstance(event, Stage):
            self._stage(event)
        elif isinstance(event, Model):
            self._model(event)
        elif isinstance(event, Retrieval):
            self._retrieval(event)
        elif isinstance(event, Claim):
            self._claims.append(event.text)
        elif isinstance(event, Done):
            self._outcome = "cached" if event.cached else event.outcome

    def _stage(self, event: Stage) -> None:
        if event.status == "start":
            run = self._root.create_child(name=event.name, run_type="chain", inputs={})
            run.post()
            self._open[event.name] = run
            self._last = run
        elif event.name in self._open:
            run = self._open.pop(event.name)
            run.end(outputs={"ms": event.ms})
            run.patch()

    def _model(self, event: Model) -> None:
        parent = self._open.get(event.stage, self._last)
        run = parent.create_child(
            name=event.model,
            run_type="llm",
            inputs={},
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
            }
        )
        run.patch()

    def _retrieval(self, event: Retrieval) -> None:
        documents = [
            {"page_content": hit.title, "metadata": {"id": hit.id, "score": hit.score}} for hit in event.hits
        ]
        run = self._last.create_child(name="retrieval", run_type="retriever", inputs={})
        run.post()
        run.end(outputs=self._redactor.walk({"documents": documents}))
        run.patch()

    def finish(self, facts: TraceFacts) -> None:
        """End the root run, with the outcome, the claims and what `route` decided; all redacted."""
        self._root.end(
            outputs=self._redactor.walk({"outcome": self._outcome, "claims": self._claims}),
            metadata=self._redactor.walk(
                {
                    "intent": facts.intent,
                    "lang": facts.lang,
                    "corpus_version": facts.corpus_version,
                    "model_config_version": facts.model_config_version,
                }
            ),
        )
        self._root.patch()

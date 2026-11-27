# Plan for F01-T10: Trace recording

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

Every question that reaches the stream leaves a stored, redacted record of how it was answered: the steps
with their models, tokens, cost and time in `trace_spans`, and the question, the sources and the claims in
`public_traces.payload`, plus its `questions_log` row. The same step events that feed the visitor's stream
feed the stored trace and, when a key is set, LangSmith. Visitors see no change; the trace pages are F02.

## Criteria

### AC1

**Mark:** HTTP test

**Scenario:** Given a covered question answered through route, retrieve, rerank, assess, answer and verify,
when the stream ends, then the stored trace has one span per `stage` end of the stream, with its step name
and `ms`, and with the model, provider, tokens and cost of that step's `model` events; the trace's cost is
the sum of the `model` events' cost and equals `usage.cost_usd`; its `events` hold the stream's `retrieval`
and `claim` events in order. Given a LangSmith key and `LANGSMITH_ENDPOINT` pointing at the fake ingest
endpoint, the fake receives a root run whose id is `done.trace_id` and one child run per `stage` start, in
order. Given no key, with `LANGSMITH_TRACING=true` set, the fake receives nothing (LangChain's ambient
tracing stays off). Given the fake endpoint hanging, the stream sends the same events, ends within the
request deadline, and the trace is stored.

**Planned test:** e2e of the API, `tests/api/test_trace.py`, "the stored trace comes from the stream's
events", "LangSmith gets the same steps", "LangSmith is off without a key", "a hung LangSmith never holds the
stream"; the existing "the trace sink receives the streamed events" (`tests/api/test_ask.py`) stays.

### AC2

**Mark:** HTTP test

**Scenario:** Given a recording sink that looks up the request's trace in the database on every event it
receives, when a visitor asks, then no `public_traces`, `trace_spans` or `questions_log` row with that trace
id exists while events are sent, and once the stream ends there is one `public_traces` row, its spans and one
`questions_log` row with the same outcome. Given a trace whose `questions_log` row the database refuses, when
it is written, then the write raises "unavailable" and no `public_traces` or `trace_spans` row is left. Given
a store that fails, the visitor's stream still ends with `done` and the failure is logged with the trace id.

**Planned test:** e2e of the API, `tests/api/test_trace.py`, "the trace is written after the stream", "a
failed trace write still ends the stream"; integration, `tests/core/test_trace_store.py`, "a trace is
written in one transaction".

### AC3

**Mark:** HTTP test

**Scenario:** Given a question with an email, a phone number with its country code and a valid CPF, and a
scripted answer whose claims repeat the email, carry a zero-width character and quote one of the API's
secret values, when the stream ends, then no stored row (`public_traces`, `trace_spans`, `questions_log`)
and nothing the fake LangSmith endpoint received holds any of them or any line of the domain's prompts, and
the placeholders `[email]`, `[phone]`, `[cpf]` and `[secret]` stand in their place. Given an email in the
domain's `pii_allowlist`, it stays. Given a run of 32 letters and digits, a key with a known prefix or a JWT,
the redactor replaces it; given an ordinary long word or a URL, it doesn't.

**Planned test:** e2e of the API, `tests/api/test_trace.py`, "the stored trace and LangSmith are redacted";
unit, `tests/core/test_redact.py`, one case per rule, "the allowlist passes", "the whole payload is walked".

### AC4

**Mark:** HTTP test

**Scenario:** Given each ending a run can reach in F01 so far (an answer, an abstention, a decline,
search-only in a degraded mode, search-only after the models failed, and a bug that ends with `error` then
`done`), when the stream ends, then `public_traces` has the row `done.trace_id` names, with the stored
outcome (`answered`, `abstained`, `declined`, `degraded`, `degraded`, `error`), and `public_ok`, `intent` and
`lang` from `route` when it answered (false and null otherwise).

**Planned test:** e2e of the API, `tests/api/test_trace.py`, "every ending leaves the trace done names"
(parametrized by ending).

### AC5

**Mark:** HTTP test

**Scenario:** Given the fake provider answering 429 for the first free entry of the `route` stage, when a
visitor asks, then the `route` step's `model` event names the second entry with `fallback_reason:
"rate_limited"`, and the stored `route` span has the same model and reason; the other steps, answered by
their first entry, have a null reason in both. Given the first entry timing out, the reason is `timeout`;
given it cooling down from an earlier request, `cooling_down`.

**Planned test:** e2e of the API, `tests/api/test_trace.py`, "a fallback is recorded in the model event and
the span" (parametrized by the first entry's failure); unit, `tests/core/test_gateway.py`, "the completion
names why the earlier entry didn't answer" (`budget` and `request_cap` for the paid entry).

### AC6

**Mark:** HTTP test

**Scenario:** Given a session that has used its burst, when it asks again, then the response is `429
rate_limited` and, once the event log drains, `log_events` has one row with level `warning`, component
`gate`, event `rate_limited`, the empty buckets' kinds in `fields`, a null trace id, and no key, IP or
session id in any column.

**Planned test:** e2e of the API, `tests/api/test_gate.py`, "a rate limit leaves a log_events row".

## Contract

### model · server → client · altered

- Added: `tokens_in` (integer), `tokens_out` (integer), `cost_usd` (number): the usage and cost of the call
  the event names, as `usage` sums them. Not required in the schema, so streams recorded before stay valid;
  the API always sends them.
- Changed: `fallback_reason` (string or null, optional) is set when an earlier entry of the stage's chain
  didn't answer: `cooling_down`, `rate_limited`, `timeout`, `failed`, `budget` or `request_cap`, the last
  one's; null when the first entry tried answered.

### public_traces · zorzo · altered

- Changed: `public_traces_outcome_check` gains `error`: `outcome IN ('answered', 'abstained', 'declined',
  'blocked', 'degraded', 'cached', 'error')`.
- No column change. `payload` gets the shape of LLD "4. Data", "Stored trace".

### questions_log · zorzo · altered

- Changed: `questions_log_outcome_check` gains `error`, as above.

Both checks change through `OUTCOMES` in `data/models.py` and a generated migration (`0005`), not
hand-written SQL. `trace_spans` is written for the first time, with no change. `log_events` gets
`rate_limited` rows (component `gate`), with no change. No route changes.

## Contract verification

- `model` carries each call's tokens and cost, and the spans and the trace's cost come from them: AC1.
- `model.fallback_reason` and `trace_spans.fallback_reason`: AC5.
- `log_events` `rate_limited` rows: AC6.
- A `public_traces` row per ending, outcome `error` included: AC4.
- One `questions_log` row per trace, written with it: AC2.
- `trace_spans` rows per ended step: AC1; written with the trace or not at all: AC2.
- Redacted `payload`, `questions_log.question_scrubbed` and spans: AC3.

## Decisions

- **Library before own code.** What each candidate covers, checked in the installed code (langsmith 0.16.2,
  langchain-core 1.8.4, litellm 1.108.5) where it weighs on the choice:
  - **LangSmith SDK, adopted for the transport.** `Client.create_run` (which `RunTree.post` and `patch`
    call) builds the run and puts it on the client's in-memory queue with `put_nowait`, dropping it when the
    queue is full (`_put_tracing_queue`); a background thread batches and posts, with its own retries.
    Nothing on the request's path waits on LangSmith. `RunTree.post` doesn't consult the ambient tracing
    switch. The runtime metadata it adds reads only `LANGSMITH_*` and `LANGCHAIN_*` variables, without keys
    (`get_langchain_env_var_metadata`); `omit_traced_runtime_info=True` drops it anyway. The client fetches
    `/info` lazily in its thread; passing `info` skips that call and, with it, zstd compression, so the
    fake endpoint reads plain multipart. Ours: mapping the step events to runs.
  - **LangChain's ambient tracing (`LangChainTracer`), rejected and forced off.** With `LANGSMITH_TRACING`
    set, langchain-core attaches it to every runnable (`_tracing_v2_is_enabled` →
    `langsmith.utils.tracing_is_enabled`), so the graph would trace itself: its inputs and outputs are the
    graph state (the raw question, passages, the answer contract), unredacted. It doesn't see the LiteLLM
    calls (not LangChain models) or the search-only path outside the graph, and it would be a second
    instrumentation next to the step events. `langsmith.configure(enabled=False)` sets the global switch,
    which `tracing_is_enabled` reads before the environment.
  - **LiteLLM's LangSmith callback, not used.** It sends each call's messages and output, unredacted, and
    runs only when `litellm.callbacks` names it; the adapter sets no callback.
  - **LangSmith's anonymizer (`create_anonymizer`), not used.** A regex walker over the run's dicts; the
    trace is redacted at the source, so one redactor serves Postgres and LangSmith.
  - **OpenTelemetry (with LangSmith's OTel mode or an OTLP exporter), rejected.** It adds an SDK and an
    exporter for one backend LangSmith's own client already reaches without blocking, and has no exporter
    for our trace tables; `opentelemetry-api` is in the tree only through FastAPI.
  - **Langfuse, rejected.** The HLD's technology constraint is LangSmith (the site shows it in production),
    and a self-hosted Langfuse needs ClickHouse, Redis and object storage on an 8 GB VPS.
  - **PII: phonenumbers, already pinned, reused.** The ingest PII pass already finds emails, phones (BR and
    US plans), CPFs and card numbers. Presidio (not installed) needs a spaCy model of hundreds of MB and
    detects names, which the questions are full of (André's, employers'); scrubadub (not installed) is
    English-centric and barely maintained.
  - **Secrets: detect-secrets, not adopted** (not installed; verdict from its documented scope). Its plugins
    and baselines are built for files in a repository; for a question of at most `MAX_QUESTION_CHARS` and a
    few claims, the exact secret values plus a handful of key shapes cover the case. gitleaks scans folders
    from a subprocess, too heavy per request.
- **The one handler is the TraceSink fan-out** (F01-T02's decision, consumed): `FanOutSink` gets each step
  event once and hands it to the stream, the trace recorder and the LangSmith sink, in that order. The LLD's
  "custom LangChain CallbackHandler" row predates that decision and is updated.
- **A recorder per request, written by the route.** `TraceRecorder` is a TraceSink that keeps each event
  with the wall time it arrived (record is synchronous, so that is when it was sent). After the run
  returns, the route turns it into a `StoredTrace` with the request's facts, which no stream event carries:
  the scrubbed question, `intent`, `lang` and `public_ok` from `route` (`RunResult` gains `intent` and
  `language`), the corpus version and `model_config_version`. `run_agent` and `run_search_only` don't
  write, so an eval run (F01-T11) writes no public trace unless it wires a store.
- **Spans from `stage` and `model`.** A span per `stage` end: `started_at` is when its start arrived,
  `latency_ms` is the end's `ms`; the `model` events with that step's name sum into its tokens and cost, and
  the last one names the model and provider (an answer asked again with the schema is two calls in one
  step). A step that started and never ended gets no span (it would skew the latency chart) and is named in
  `payload.cut`. The trace's `cost_usd` is the sum of the `model` events' cost, so a search-only run that
  spent on `route` still shows it, though it sends no `usage`; `latency_ms` runs from the request's start to
  `done`.
- **The payload keeps the events the page shows, generically.** Every event except `stage`, `model`,
  `token` (the draft, which the claims replace), `usage` and `done` goes in `events` as `{kind, …}`, so the
  `guard`, `blocked` and later events of F01-T07 to F01-T09 are stored with no change here. `payload.done`
  is `done`'s payload without `trace_id` and `outcome` (they have columns), so the optional fields `done`
  gains (`contact` and `suggestion` from F01-T08, `notice` from F01-T07, `cached` from F01-T09) are stored
  as sent, redacted, with no change here either.
- **The stored outcome** is `cached` when `done.cached` is true, otherwise `done.outcome`, as LLD "Stream
  events" names them (the mapping agreed with F01-T09). `model_config_version` is stored as the app computed
  it; F01-T09 makes it hash the prompts and the intents.
- **Written after `done`, before the stream closes.** `events()` already awaits the run's task after
  `done`; the task now writes the trace before it ends, so the stream closes once the row exists (AC4) and
  nothing waits on the database while events are sent (AC2). The write runs as a task of the app's
  `BackgroundTasks`, awaited through `asyncio.shield`, so a visitor who leaves after `done` doesn't cancel
  it, and shutdown drains it within `SHUTDOWN_DRAIN`. It uses `Database.short_transaction`, whose statement
  and lock timeouts bound it. A failure logs `trace_write_failed` at `error` with the trace id.
- **No trace without `done`.** A visitor who leaves before `done` cancels the run, and the recorder never
  sees an ending: nothing is written, since no outcome is known; the spend is already in `budget_daily`
  (settled per call). A gate refusal (422, 403, 429, 503) is not a reply and leaves no trace either: its
  `trace_id` is for the logs, and refused traffic writes nothing.
- **"The final cost" is the trace's cost.** The day's spend is settled per call by the gateway (LLD "6.2
  Daily budget"), and settling it again at the end would count it twice; the LLD's "Transactions" line now
  says so.
- **Redaction in one place, at the source.** `Redactor(allowlist, secrets)` is built at startup with the
  domain's `pii_allowlist` and every secret value in the API's settings (Turnstile, salt seed, provider and
  Telegram keys, the LangSmith key); F01-T08 hands it the canary when it adds that setting. The question is
  scrubbed once; the stored payload is walked, every string redacted; the LangSmith sink redacts what it
  sends with the same instance. The detectors move from `ingest/gate.py` to `rag_core/pii.py`, shared by
  the ingest (`personal_data`, rules) and the redactor (`scrub`, replacing each match). T10 merges first
  (merge order T10, T08, T07, T09, T11), so `rag_core/pii.py` and `services/redact.py` are the shared
  modules F01-T07, T08 and T09 use instead of their own detectors. The phone matcher runs with
  `Leniency.VALID`, as in the ingest, on the text as the trace stores it. Redacting fails closed: a long
  token that isn't a key is replaced too.
- **The LangSmith run tree mirrors the steps.** Root run `ask` (`chain`), id = the trace id, so the admin's
  link (LLD "Dashboard pages") is the trace id; inputs `{question}` scrubbed; a child run per `stage` start,
  ended at its end; `model` events become `llm` child runs of their step with `usage_metadata` and the
  model and provider as metadata; `retrieval` a `retriever` child with ids, titles and scores; the root
  ends at `done` with `{outcome, claims}` redacted and metadata `{intent, lang, corpus_version,
  model_config_version}`. `token` is not sent.
- **LangSmith's settings.** `LANGSMITH_API_KEY` (secret, optional) turns it on; `LANGSMITH_PROJECT` and
  `LANGSMITH_ENDPOINT` are passed to the client when set. The API calls `langsmith.configure(enabled=False)`
  at startup with or without a key. `LANGSMITH_TRACING` is no longer read; sampling (HLD risk "LangSmith
  free plan") stays the SDK's own `LANGSMITH_TRACING_SAMPLING_RATE`, unset.
- **The trace's expiry is stamped at write.** `expires_at = now + RETENTION_PUBLIC_TRACES`
  (`retention_public_traces` in `limits.yaml`, in seconds like the others), so the retention job deletes by
  `expires_at` and needs no copy of the value.
- **`langsmith` becomes a direct dependency of `rag-core`**, pinned to the version `uv.lock` already
  resolves (0.16.2, through langchain-core), and joins the import contract "only core adapters use network
  clients".
- **The `model` event carries its call's tokens and cost** (owner-approved, 2026-11-27). Per-step tokens
  and cost must come from the step events (AC1), and only `usage` carried them, summed. Discarded: an
  internal event the stream doesn't send (it breaks F01-T02's rule that every sink receives the same
  events) and reading `AgentContext` (a second source). The three fields are optional in the schema, and
  LLD "Stream events" now lets a task before launch add optional fields to an event.
- **`error` is a stored outcome** (owner-approved). `done.outcome` can be `error` (a bug), `events.py` left
  the mapping to this task, LLD "Front against the API" links the trace on `error`, and AC4 needs the row.
  Discarded: storing it as `abstained` (a bug would count as an honest abstention) and writing no trace (the
  front's link would 404). Both checks gain `error` in migration `0005` (the number this task takes);
  `/dash/` shows recent questions only for `answered`, `abstained` and `cached`, so nothing public changes.
- **Fallbacks are recorded in the trace** (owner-approved, AC5). F02's fallback chart reads
  `trace_spans.fallback_reason`, and HLD "6.2 Degradation" says every fallback is recorded in the trace.
  The gateway's `Completion` carries the reason the last earlier entry of the walk didn't answer:
  `cooling_down` (skipped in cooldown), `rate_limited` (a 429), `timeout`, `failed` (any other provider
  failure or a model miss after its retry), `budget` (the paid entry's reservation refused) or
  `request_cap`; `model` sends it and the span stores it. `rerank` sends no `model` event, so its fallback
  to the local level stays out of this task.
- **Per-request catalog rows go through the EventLog** (owner-approved, AC6). A rate limit happens before
  any stream or trace, so the gate writes `rate_limited` (with the empty buckets' kinds, never a key)
  through the EventLog port with no trace id, in the background like the cooldown rows. A fallback is
  already in its span and `model` event, so it gets no `log_events` row. LLD "Logs" says so.
- **Names are not scrubbed** (owner-approved). The redactor finds contact identifiers, document and card
  numbers and secrets, not a visitor's name written in a question: name detection needs an NER model and
  would also strike André's and his employers' names. Such a question stays in `questions_log` and the
  trace; `route` judges personal data as a content category, so `public_ok` is false and public pages show
  it hidden, and retention deletes it. HLD "Security and privacy" says what scrubbing finds.
- **LangSmith gets no prompts** (owner-approved). From the step events it gets steps, models, tokens, cost,
  latency, sources and redacted claims, which is what HLD "Observability" lists; no prompt or raw model
  output. Sending them would need a second path from the LiteLLM adapter and a redactor over prompts that
  hold passages and the visitor's text. Prompts are inspected in eval runs; LLD "Logs" says full prompts are
  never stored, logged or sent.
- **A row that names the trace joins the end-of-request transaction.** F01-T09 leaves
  `answer_cache.trace_id` null. When a later change sets it, the cache entry is written in the same
  transaction as the trace, after the trace row, so its foreign key always holds and a failed trace write
  leaves no entry pointing at nothing; writing it before (during the stream) would break the foreign key,
  and after (a second transaction) could leave a trace without its entry or the reverse. This task builds
  no hook for it; LLD "Transactions" records the rule.

## Files and interfaces

- `packages/core/src/rag_core/events.py`: `Model.tokens_in`, `Model.tokens_out`, `Model.cost_usd`;
  `contracts/sse-v1.schema.json` regenerated.
- `packages/core/src/rag_core/agent/context.py`: `record` sends the call's tokens, cost and fallback
  reason in `model`.
- `packages/core/src/rag_core/agent/graph.py`: `RunResult(outcome, public_ok, intent, language)`.
- `packages/core/src/rag_core/services/gateway.py`: `Completion.fallback_reason`, set by the chain walk
  in `complete` and `stream`.
- `packages/core/src/rag_core/services/trace.py`: `FanOutSink` (kept); `TraceRecorder(started_at)` with
  `finish(facts, redactor) -> StoredTrace | None` (None without `done`); `TraceFacts`, `StoredTrace`,
  `SpanRow`.
- `packages/core/src/rag_core/pii.py` (new): the detectors from `ingest/gate.py`; `personal_data(text,
  allowlist) -> list[str]`, `scrub(text, allowlist) -> str`.
- `packages/core/src/rag_core/services/redact.py` (new): `Redactor(allowlist, secrets)`, `.text(str) ->
  str`, `.walk(obj) -> obj`.
- `packages/core/src/rag_core/ingest/gate.py`: imports the detectors from `rag_core.pii`; `personal_data`
  still importable from it.
- `packages/core/src/rag_core/ports/__init__.py`: `TraceStore.write(trace: StoredTrace)`; the TraceSink
  docstring.
- `packages/core/src/rag_core/adapters/trace_store.py` (new): `PostgresTraceStore(db)`.
- `packages/core/src/rag_core/adapters/langsmith_tracer.py` (new): `LangSmithTracer(api_key, project,
  endpoint)`, `.sink(trace_id, question) -> TraceSink`, `.flush(timeout)`; `disable_ambient_tracing()`.
- `packages/core/src/rag_core/adapters/factory.py`: `build_tracer(settings) -> LangSmithTracer | None`.
- `packages/core/src/rag_core/background.py`: `spawn` returns the task, so the route can await it shielded.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: `retention_public_traces`.
- `packages/core/src/rag_core/services/gate.py`: `Gate` gets the `EventLog` and writes the `rate_limited`
  row; `apps/api/src/rag_api/main.py` passes the gateway's event log.
- `apps/api/src/rag_api/settings.py`: `langsmith_api_key: SecretStr | None`, `langsmith_project`,
  `langsmith_endpoint`; `secret_values()` for the redactor.
- `apps/api/src/rag_api/ask.py`: the recorder and the LangSmith sink per request; the trace written after
  `done`.
- `apps/api/src/rag_api/main.py`: trace store, redactor and tracer wired; ambient tracing off; shutdown
  drains the trace writes and flushes LangSmith.
- `apps/dashboard/data/models.py`: `OUTCOMES` gains `error`; `data/migrations/0005_*.py` generated.
- `packages/core/pyproject.toml`: `langsmith==0.16.2`; root `pyproject.toml`: `langsmith` in the
  network-client import contract.
- `infra/compose.yaml`, `infra/.env.example`: `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` for the API, empty.
- `tests/fakes/langsmith.py` (new): fake ingest endpoint, recording bodies or hanging.

## Existing tests that change

- `tests/contract/test_sse_schema.py`: the regenerated schema (the `model` fields).
- `tests/api/test_answer.py`, `tests/api/test_ask.py`: `model` events carry the new fields; nothing they
  assert changes, and the static `app.state.trace_sinks` stays for their recording sinks.
- `tests/core/test_graph.py`: `RunResult` gains `intent` and `language`.
- `tests/schema/test_migrations.py`: the reflection test sees the new checks.
- `tests/core/test_gateway.py`: completions carry `fallback_reason`; existing assertions unchanged.
- `tests/ingest/test_gate.py`: unchanged; `personal_data` is still importable from the gate.

## Data change

None. No trace exists yet; the changed checks only widen what they accept.

## Updated documents

- `docs/lld.md`:
  - "1. Code structure", Tracing row: the TraceSink fan-out and the langsmith `Client`/`RunTree`; ambient
    tracing off, and why.
  - "2. Components and dependencies", Ports: the TraceSink row (one fan-out, LangSmith only with a key, the
    queue that drops); new TraceStore row.
  - "Security": new "Trace redaction" item.
  - "Transactions", end of a request: "the final cost" is the trace's; written after `done`, before the
    stream closes; shielded; no trace without `done` or for a gate refusal; a failed write; a row that
    names the trace joins the transaction.
  - "Logs": a rate limit and the cooldowns go through the EventLog, a fallback only to its span and
    `model` event; full prompts are never stored, logged or sent.
  - "Configuration", Shutdown: the trace writes and LangSmith's queue drain within `SHUTDOWN_DRAIN`.
  - "4. Data": `public_traces` (outcome list gains `error`, stored outcome, `expires_at`, cost and latency,
    payload), `trace_spans` (fallback reason) and `questions_log` notes; new "Stored trace" paragraph with
    the payload's shape, `done`'s optional fields included.
  - "Stream events": a task before launch may add optional fields to an event.
  - "9. Values and configuration": `RETENTION_PUBLIC_TRACES` stamps `expires_at`; the LangSmith variables
    (`LANGSMITH_TRACING` dropped, `LANGSMITH_ENDPOINT` added).
  - "10. Development environment": the fake LangSmith endpoint.
- `docs/hld.md` (through the `hld` skill's update mode):
  - "Security and privacy", Privacy: what scrubbing and redaction find, and that a name in a question is
    not detected but hidden from public pages and deleted with its retention.
  - "7. Data": the rule that no visitor personal data is stored is qualified by what scrubbing can find.

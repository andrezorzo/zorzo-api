# Plan for F01-T05: Model gateway and budget

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** integration test

**Scenario:** Given a stage whose chain has three entries on the fake OpenRouter server, when the first
answers, then the result carries its text, usage, cost and provider, and the other two get no request.
Given the first entry failing (5xx, timeout, malformed body), then the second answers and the result names
it. Given an entry whose breaker is open, then it gets no request. Given a paid entry whose estimate doesn't
fit in what the day or the request has left, then it gets no request. Given a deadline shorter than an
entry's hang, then the call ends at the deadline, not at the stage timeout. Given every entry failing or
skipped, then the gateway raises `ModelsUnavailable`. For `stream`, a failure before the first token falls
to the next entry; a failure after it raises `ModelsUnavailable` with the tokens already given kept by the
caller.

**Planned test:** integration (real test database, fake OpenRouter), `tests/core/test_gateway.py`, "answers
from the first entry", "falls to the next entry on failure", "skips an open breaker", "skips a paid entry the
budget can't cover", "stops at the deadline", "raises when the chain is exhausted", "stream falls back only
before the first token".

### AC2

**Mark:** unit test, integration test

**Scenario:** Breaker: given `BREAKER_FAILURES` failures within `BREAKER_WINDOW` on one entry (fake clock),
then it opens; given one 429, then it opens at once; after `BREAKER_COOLDOWN`, one trial call goes through
(half-open) and closes it on success or reopens it on failure; every transition leaves a `log_events` row.
Budget: given today's spend below `BUDGET_PAID_CUTOFF × DAILY_BUDGET_USD`, paid entries run; once settled
spend reaches the cutoff, `paid_disabled_at` is set and paid entries are skipped; at `DAILY_BUDGET_USD`,
`degraded_at` is set; a new UTC day starts at `normal`. Given concurrent reservations against one row, then
the settled spend never passes the cap. Given a failed paid call, then its reservation is released. Free
quota: given a provider past `QUOTA_WARN` of its daily limit, then the rerank stage skips its remote entries,
chat stages skip that provider's free entries (paid entries still run if the budget allows), and with no
entry left the mode is `quota_exhausted`.

**Planned test:** unit, `tests/core/test_breaker.py`, "opens after repeated failures", "opens on one 429",
"half-open trial closes or reopens"; integration, `tests/core/test_budget.py`, "moves through the day's
states", "concurrent reservations never cross the cap", "a failed call releases its reservation", "a new
day starts normal"; integration, `tests/core/test_gateway.py`, "saves free quota past the warning",
"breaker transitions are logged".

### AC3

**Mark:** HTTP test

**Scenario:** Given today's budget row at the cap, when a visitor asks, then the stream sends `degraded`
with reason `budget_exhausted`, then its `retrieval` hits and `done`, and the fake OpenRouter server gets no
request (no remote rerank either). Given every entry of the `answer` chain with an open breaker (opened by a
gateway call against the fake in 429 mode), then the same stream with reason `models_unavailable` and no new
request. `GET /api/healthz` answers `mode: degraded` in both cases.

**Planned test:** e2e of the API, `tests/api/test_degraded.py`, "budget at the cap answers search-only with
no model call", "providers down answers search-only with no model call", "health reports degraded".

### AC4

**Mark:** integration test, HTTP test

**Scenario:** Given the budget reaching the cap, then `alert_states` opens `budget_exhausted` and the
Alerter sends one message; given ten concurrent questions in that state, still one message. Given the
`answer` chain fully open, then `providers_down` opens once. Given the condition still present after
`ALERT_REPEAT_AFTER` (fake clock), then one reminder; given it cleared (a new UTC day, an `answer` entry
closing), then one resolved message and the row closes. A failing Alerter never fails the question.

**Planned test:** integration, `tests/core/test_alerts.py`, "opens once under concurrency", "reminds after
the repeat interval", "closes when the condition clears", "a failing alerter is logged, not raised"; e2e of
the API, `tests/api/test_degraded.py`, "budget at the cap alerts once".

### AC5

**Mark:** HTTP test

**Scenario:** Given the fake OpenRouter server ranking the candidates in reverse of the local order, when a
visitor asks, then the `retrieval` hits follow the remote order. Given the remote rerank failing (5xx,
hang past `TIMEOUT_RERANK`, malformed body), then the hits follow the local reranker and the stream ends
normally. Given the rerank provider past `QUOTA_WARN`, then the remote level gets no request.

**Planned test:** e2e of the API, `tests/api/test_ask.py`, "remote rerank leads", "falls to the local
reranker when the remote one fails", "rerank goes local past the quota warning".

### AC6

**Mark:** integration test

**Scenario:** Given the fake OpenRouter server scripted per model, then it answers chat completions (JSON and
SSE streaming) with usage, rerank results, 429 with `Retry-After`, 5xx, hangs and malformed JSON, and records
every request (path, model, body) so a test can assert which entries were called. AC1, AC2, AC3 and AC5 run
against it.

**Planned test:** the fake lives in `tests/fakes/openrouter.py`; its behaviors are exercised by the AC1, AC2,
AC3 and AC5 tests (no separate test of the fake).

### AC7

**Mark:** unit test, HTTP test

**Scenario:** Given `config/models.yaml` with stages, chains and prices, when the API starts, then it loads
into a validated model; an unknown stage, a paid entry without prices, a provider without a base URL, or (in
production) a provider without its key stops startup. With `APP_ENV=local`, `config/models.local.yaml` is
loaded instead. Given the same files with one price changed, then `model_config_version` changes; given only
a comment or key order changed, then it doesn't.

**Planned test:** unit, `tests/core/test_models_config.py`, "loads stages, chains and prices", "rejects an
invalid file", "version follows content, not formatting"; e2e, `tests/api/test_startup.py`, "refuses a
provider without its key in production", "local profile loads models.local.yaml".

## Contract

### POST /api/v1/ask · altered

| Item | Before | After |
| --- | --- | --- |
| Success | Always `degraded {mode: search_only, reason: models_unavailable}`, then `retrieval`, `done` | `reason` is `budget_exhausted` (budget at the cap), `quota_exhausted` (no `answer` entry within quota and budget) or `models_unavailable` (every `answer` entry open, or until the agent graph exists, F01-T06); the remote rerank level runs only when the mode is normal |

No status or error changes. The `degraded` payload is unchanged: the three reasons already exist in
`contracts/sse-v1.schema.json`.

### GET /api/healthz · altered

| Item | Before | After |
| --- | --- | --- |
| Success | `mode: degraded` when today's budget row has `degraded_at` | `mode: degraded` whenever a question would be answered search-only for a reason other than the missing agent graph: budget at the cap, every `answer` entry open, or no `answer` entry within quota |

No table changes: `budget_daily`, `alert_states` and `log_events` exist (F01-T01).

**Config files:** `config/models.yaml` and `config/models.local.yaml` (new; schema below).

```yaml
providers:                       # every OpenAI-compatible endpoint the chains use
  openrouter:
    base_url: https://openrouter.ai/api/v1
    key_env: OPENROUTER_API_KEY  # absent for a keyless provider (Ollama)
    daily_requests: 1000         # free-request limit per UTC day, for QUOTA_WARN; absent = no limit
stages:                          # exactly route, rerank, answer, judge
  answer:
    max_tokens: 700              # output cap of every entry; the estimate uses it
    chain:                       # tried in order
      - {provider: openrouter, model: "vendor/model:free"}
      - {provider: openrouter, model: "vendor/model", paid: true,
         price: {input_per_mtok: 0.10, output_per_mtok: 0.40}}   # US$ per million tokens
```

**Settings (API):** `MODELS_FILE` (default `config/models.yaml`, or `config/models.local.yaml` with
`APP_ENV=local`); `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `NVIDIA_API_KEY`, `GOOGLE_AI_API_KEY` (optional
`SecretStr`, required in production when a chain uses the provider); `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID` (`SecretStr`, required in production; locally the Alerter only logs).
**Rule values (`limits.yaml`):** `timeout_route`, `timeout_rerank`, `timeout_answer`, `timeout_judge`,
`daily_budget_usd`, `budget_paid_cutoff`, `request_cost_cap_usd`, `quota_warn`, `breaker_failures`,
`breaker_window`, `breaker_cooldown`, `alert_repeat_after`, with the LLD values.

## Contract verification

- `POST /api/v1/ask`, `degraded` with `budget_exhausted` and `models_unavailable`, no model call: AC3.
- `degraded` with `quota_exhausted`: AC2 ("saves free quota past the warning" asserts the mode); not driven
  through the route, since the route's degraded path is the same code with another reason.
- Remote rerank only in normal mode, falling to local: AC5, AC3.
- `GET /api/healthz` `mode: degraded` from the gateway's mode: AC3.
- `budget_daily` conditional reservation (never past the cap under concurrency): AC2.
- `alert_states` conditional open (one message under concurrency): AC4.
- `log_events` rows for breaker transitions: AC2.
- `models.yaml` schema and `model_config_version`: AC7.

## Decisions

- **Gateway module:** `rag_core.services.gateway.ModelGateway` holds the rules (chain walk, deadline,
  budget, breakers, quota); `rag_core.adapters.openai_compat.OpenAICompatProvider` is the only network
  client: one class for every provider (OpenRouter, Groq, NVIDIA NIM, Google AI Studio, Ollama all speak
  the OpenAI chat API), plus `POST /rerank` for rerank entries. httpx directly, no `openai` SDK: one more
  dependency for a few endpoints, and its retries and timeouts would have to be turned off to keep ours.
- **Port operations:** `complete(stage, messages, *, schema=None, deadline_at, spend)` and
  `stream(...)` return or yield text, usage, cost and the entry used; `rerank(query, documents, *,
  deadline_at)` returns scores; `mode() -> DegradedReason | None` says whether questions go search-only now.
  `spend` is a per-request `RequestSpend` that holds the request's paid total against
  `REQUEST_COST_CAP_USD`. The LLD port row gains `rerank`, `mode` and `spend`.
- **Per-entry timeout:** `min(TIMEOUT_<STAGE>, deadline_at − now)`, under `asyncio.timeout`; an entry with
  no time left is not tried.
- **What a failure is:** timeout, transport error, 5xx and a malformed body count toward the breaker and
  fall to the next entry; a 429 opens the breaker at once and falls through; there is no same-entry retry
  (the next entry is the retry). Output that parses but fails the requested schema is retried once on the
  same entry, then falls through, and never counts toward the breaker (the provider works; the model
  missed). A "context too long" error is logged at `error` and falls through (LLD "6.3 Agent run").
- **Breaker:** keyed by (provider, model), in process memory, monotonic clock injected for tests; half-open
  lets exactly one trial call through while the others skip the entry. Transitions go to `log_events`
  through a new `EventLog` port (`write(level, component, event, fields)`, Postgres adapter, written in a
  background task so a call never waits on it) and to structlog (`warning` on open, `info` on close). The
  TraceSink carries per-request step events, and a breaker transition belongs to no request.
- **Budget:** `rag_core.adapters.budget.PostgresBudget` with `reserve(estimate, now) -> bool` (one
  conditional `INSERT … ON CONFLICT DO UPDATE … WHERE cost_usd + estimate <= cap AND paid_disabled_at IS
  NULL`, which also counts the request) and `settle(estimate, actual, now)` (adds `actual − estimate` and
  sets `paid_disabled_at` / `degraded_at` when the settled spend crosses the cutoff / the cap). A failed
  call settles with actual 0. Prompt tokens for the estimate: `ceil(characters / 3)`, conservative for
  English and Portuguese, no tokenizer dependency. Free entries never touch the row.
- **Free quota:** an in-memory counter per provider per UTC day, replaced by the provider's
  `x-ratelimit-remaining-requests` / `x-ratelimit-limit-requests` headers when a response has them. A
  restart forgets the count; the provider's 429 then opens the breaker, so the cost is one failed call.
  OpenRouter's key endpoint is not polled (one more request per question, or a timer, for the same number).
- **Mode, checked by the route after the gate:** budget row `degraded` → `budget_exhausted`; no `answer`
  entry callable (all breakers open) → `models_unavailable`; no `answer` entry within quota and budget →
  `quota_exhausted`; otherwise normal, which until F01-T06 still answers search-only with
  `models_unavailable` and the remote rerank in front. In a degraded mode the rerank chain is the local
  level only, so the question makes no model call.
- **Rerank chain:** the `Reranker` port becomes async; `GatewayReranker` (calls `gateway.rerank` on the
  `rerank` stage) leads, `LocalReranker` (now wrapping its own `anyio.to_thread` call) follows, then the
  fused order. `retrieval.rerank` stays the place that falls through.
- **Alerts:** `rag_core.services.alerts.Alerts(store, alerter, clock)` owns the incident rules: `raise_(kind,
  message)` opens through the conditional upsert on `alert_states` and sends only when it opened the row or
  the last message is older than `ALERT_REPEAT_AFTER`; `clear(kind)` closes an open row and sends the
  resolved message. The Alerter port only delivers; the LLD row moves "deduplicated" to the service.
  `budget_exhausted` is raised when the gateway sees the day `degraded` and cleared on the first check of a
  new UTC day; `providers_down` is raised when the `answer` chain is all open and cleared when an `answer`
  entry closes. Sending runs in a background task with a short timeout; a failure is logged, never raised.
  Messages hold the kind and counts, never visitor data.
- **Telegram:** `httpx` logs every request URL at `info`, and the bot token is part of Telegram's URL, so the
  `httpx` and `httpcore` loggers are set to `warning` at startup, with a test that a sent alert leaves no
  token in captured logs.
- **`model_config_version`:** SHA-256 of the canonical JSON of the loaded models config and limits (prompts
  join in F01-T06), so formatting and comments don't invalidate the answer cache. Computed at startup and
  kept on `app.state`; recording it with answers is F01-T10.
- **Stage names (owned):** `route`, `rerank`, `answer`, `judge`, the four with a timeout in LLD "Values and
  configuration".
- **Fake OpenRouter:** `tests/fakes/openrouter.py`, an httpx `MockTransport` like the Turnstile fake: offline,
  fast, and hangs and malformed bodies on demand; scripted per model with a queue of behaviors.
- **Pre-T06 reason:** a normal-mode question keeps `models_unavailable`, the reason T02 gave; F01-T06
  replaces the normal path with the graph.
- **Graph calls map onto the four stages:** the gateway's vocabulary is `route`, `rerank`, `answer` and
  `judge`; F01-T06 maps `assess` and `reformulate` to `route` (cheap, short) and `repair` to `answer`, so no
  stage exists without a timeout.
- **Remote rerank request format:** OpenRouter lists `nvidia/nemotron-3.5-rerank-8b:free` and answers
  `POST /api/v1/rerank` (401 without a key). The adapter sends the Cohere-style body (`model`, `query`,
  `documents`, `top_n`) and reads `results[{index, relevance_score}]`, as the fake does. One `network`
  contract test checks the shape against the real endpoint; it skips without `OPENROUTER_API_KEY` and runs
  only when the owner allows a free (`:free`, no cost) call. No key is set today, so the shape is unconfirmed
  until then.
- **Production chains, provisional:** `models.yaml` ships OpenRouter-only chains, two `:free` entries per
  chat stage picked from OpenRouter's model list (structured output for `route` and `judge`) plus one paid
  OpenRouter entry, marked provisional in a comment until the M3 eval matrix. Groq, NVIDIA NIM and Google
  entries join by config once their keys exist (ADR 0007: a provider is a line of config).
- **Local profile:** `models.local.yaml` points every chat stage at Ollama with `qwen3:1.7b` as a provisional
  tag (small enough for the 8 GB box), and the rerank stage has an empty chain (local cross-encoder only);
  the quick eval that settles the model stays with F01-T06. An empty chain is allowed only for `rerank`.
- **Telegram now:** the adapter is small and tested with a `MockTransport`; without it production has no
  `budget_exhausted` alert, and the env vars already exist.

## Files and interfaces

- `packages/core/src/rag_core/models_config.py` (new): `ModelsConfig`, `Stage`, `Entry`, `Provider`,
  `load_models(path)`, `config_version(models, limits) -> str`.
- `packages/core/src/rag_core/services/gateway.py` (new): `ModelGateway(config, providers, budget, breakers,
  quota, alerts, limits, clock)` with `complete`, `stream`, `rerank`, `mode`; `ModelsUnavailable`;
  `RequestSpend`; `Completion(text, parsed, usage, cost_usd, provider, model)`.
- `packages/core/src/rag_core/services/breaker.py` (new): `Breakers(limits, event_log, clock)` with
  `allow(key)`, `success(key)`, `failure(key, rate_limited)`.
- `packages/core/src/rag_core/services/quota.py` (new): `Quota(config, limits, clock)` with `over_warn(provider)`,
  `count(provider, headers)`.
- `packages/core/src/rag_core/services/alerts.py` (new): `Alerts(store, alerter, limits, clock)` with
  `raise_`, `clear`.
- `packages/core/src/rag_core/ports/__init__.py`: `ModelProvider`, `Budget`, `Alerter`, `AlertStore`,
  `EventLog`; `Reranker.rerank` async.
- `packages/core/src/rag_core/adapters/openai_compat.py` (new): `OpenAICompatProvider(base_url, key,
  transport=None)` with `chat`, `chat_stream`, `rerank`.
- `packages/core/src/rag_core/adapters/budget.py` (new): `PostgresBudget(db)`.
- `packages/core/src/rag_core/adapters/alerts.py` (new): `PostgresAlertStore(db)`, `TelegramAlerter(token,
  chat_id, transport=None)`, `LogAlerter()`.
- `packages/core/src/rag_core/adapters/event_log.py` (new): `PostgresEventLog(db)`.
- `packages/core/src/rag_core/adapters/gateway_reranker.py` (new): `GatewayReranker(gateway, limits)`.
- `packages/core/src/rag_core/adapters/local_reranker.py`, `fake_reranker.py`: async `rerank`.
- `packages/core/src/rag_core/services/retrieval.py`: awaits each level.
- `packages/core/src/rag_core/services/search_only.py`: takes the reason and the rerank chain from the route.
- `packages/core/src/rag_core/services/health.py`: mode from `gateway.mode()`.
- `packages/core/src/rag_core/services/gate.py`: docstring (the budget is the route's mode check, not a
  gate refusal).
- `packages/core/src/rag_core/adapters/factory.py`: `build_rerankers(settings, gateway)`.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: the new values.
- `config/models.yaml`, `config/models.local.yaml` (new).
- `apps/api/src/rag_api/settings.py`: `models_file`, provider keys, Telegram, production guards.
- `apps/api/src/rag_api/main.py`: builds providers, budget, breakers, quota, alerts, gateway, rerankers;
  closes the provider clients in the lifespan; sets the `httpx` loggers.
- `apps/api/src/rag_api/ask.py`: mode check after the gate, reason and rerank chain to the search-only run.
- `infra/compose.yaml`, `infra/.env.example`: `MODELS_FILE` for the local profile; Telegram vars commented.
- `tests/fakes/openrouter.py` (new); `tests/core/test_gateway.py`, `test_breaker.py`, `test_budget.py`,
  `test_alerts.py`, `test_models_config.py`, `tests/api/test_degraded.py`,
  `tests/contract/test_rerank_live.py` (new, `network`); `tests/conftest.py` (`build_app` with the fake and
  a test models file).

## Existing tests that change

- `tests/core/test_retrieval.py`, `tests/core/test_local_reranker.py`: the rerank levels are awaited.
- `tests/api/test_ask.py`: the app is built with the fake OpenRouter and a test models file; it gains the
  AC5 cases. Existing assertions on `reason: models_unavailable` stay (normal mode before F01-T06).
- `tests/api/test_healthz.py`: the degraded case comes from the gateway's mode; seeding `degraded_at` still
  drives it.
- `tests/api/test_startup.py`: production startup needs a provider key and the Telegram vars.

## Data change

None.

## Updated documents

- `docs/lld.md`, "2. Components and dependencies", Ports: ModelGateway gains `rerank`, `mode` and the
  per-request spend; Reranker is async; Alerter only delivers (the alerts service dedupes); new `EventLog`
  port.
- `docs/lld.md`, "HTTP routes": `GET /api/healthz` takes `degraded` from the gateway's mode.
- `docs/lld.md`, "Logs": breaker transitions reach `log_events` through `EventLog`; `httpx` loggers at
  `warning`.
- `docs/lld.md`, "Configuration": `model_config_version` hashes the parsed config; `MODELS_FILE`; the models
  config schema (and "Where each fact lives" points to it).
- `docs/lld.md`, "6.2 Daily budget": prompt-token estimate, release on failure, transitions on settle,
  `requests` counts paid calls.
- `docs/lld.md`, "6.6 Circuit breaker": what counts as a failure, no same-entry retry, one half-open trial.
- `docs/lld.md`, "6.8 Free quota": counter from headers or local, in memory per UTC day; no key-endpoint poll;
  `quota_exhausted`.
- `docs/lld.md`, "6.10 Alerts": when `budget_exhausted` and `providers_down` open and close; sending never
  blocks a request.
- `docs/lld.md`, "9. Values and configuration", environment variables: `MODELS_FILE`; Telegram required in
  production.
- `docs/lld.md`, "10. Development environment": `models.local.yaml` contents; the fake OpenRouter is an
  httpx `MockTransport`.
- `docs/lld.md`, "12. Open questions": "Schema of `config/models.yaml`" closed; Nemotron request format
  narrowed to quality and limits.

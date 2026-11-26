# Plan for F01-T13: Model gateway on the LiteLLM Router

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

Names follow PR #24 (exceptions end in `Error`).

## Criteria

### AC1

**Mark:** integration test

**Scenario:** Given a stage whose free chain has two entries on the fake provider server, when the first
answers, then the result carries its text, usage and provider, and the second gets no request. Given the
first answering 429 with `Retry-After: 30`, then the second answers at once (no retry on the first, no wait),
and the next call skips the first without a request. Given the first answering 5xx or hanging past its
timeout, then the second answers. After the cooldown (fake time or a short `BREAKER_COOLDOWN` in the test
limits), the first gets requests again.

**Planned test:** integration, `tests/core/test_gateway.py`, "answers from the first free entry", "a 429
cools the entry for its Retry-After and falls through at once", "a 5xx or a hang falls to the next entry",
"a cooled entry is tried again after the cooldown".

### AC2

**Mark:** integration test

**Scenario:** Given every free entry failing, when the day's budget allows the paid entry's estimate, then
one reservation is made, the paid entry answers, and the row is settled to the cost the provider reported
(OpenRouter `usage.cost`), or to the estimate when it reported none. Given a paid call the provider refused
(4xx other than 408, 503, 429), then the reservation settles at 0. Given a timeout or a 5xx on the paid call,
then the estimate stays. Given the estimate not fitting in what the day or the request has left, then the
paid entry gets no request and the call raises `ModelsUnavailableError`. Concurrent paid calls never take the
settled spend past the cap (the existing budget test).

**Planned test:** integration, `tests/core/test_gateway.py`, "pays when the free entries fail", "settles to
the reported cost", "only a refused paid call releases its reservation", "a paid call that may have run keeps
its estimate", "skips a paid entry the budget can't cover"; `tests/core/test_budget.py` unchanged.

### AC3

**Mark:** integration test

**Scenario:** Given a stream whose first free entry fails before its first chunk, then the next entry
streams the answer. Given a stream that fails after text was sent, then the caller gets
`ModelsUnavailableError` and no second answer is spliced in. Given a deadline shorter than an entry's hang,
then the call ends at the deadline. Given an estimate past what the request has left of
`REQUEST_COST_CAP_USD`, then the paid entry is not called.

**Planned test:** integration, `tests/core/test_gateway.py`, "stream falls back only before the first
token", "stops at the deadline", "the request cap stops the paid entry".

### AC4

**Mark:** HTTP test

**Scenario:** Given the remote rerank answering, when a visitor asks, then the trace shows the remote level
and the passages follow its order. Given the remote rerank failing (5xx, hang, malformed), then the local
level answers the same question.

**Planned test:** e2e of the API, `tests/api/test_ask.py`, "remote rerank leads", "falls to the local
reranker when the remote one fails" (existing, kept on the new fake server); network contract,
`tests/contract/test_rerank_live.py`, now through the Router.

### AC5

**Mark:** HTTP test, integration test

**Scenario:** Given every free `answer` entry in cooldown and the day at `paid_disabled`, when a visitor
asks, then the stream sends `degraded` with reason `models_unavailable` (or `quota_exhausted` when every one
was cooled by a 429) and the fake server gets no request; `GET /api/healthz` answers `mode: degraded`;
`providers_down` opens once. Given every free `answer` entry in cooldown while the day is `normal`, then the
mode stays normal (the paid entry can still answer). When an entry goes into cooldown, one `log_events` row
`cooldown_started` records the entry, the status that caused it and the cooldown length; when it is seen out
of cooldown, one row `cooldown_ended`.

**Planned test:** e2e of the API, `tests/api/test_degraded.py`, "providers down answers search-only with no
model call", "health reports degraded" (kept, driven by cooldowns); integration, `tests/core/test_gateway.py`,
"mode follows the budget and the cooldowns", "quota exhausted when every free answer entry was cooled by a
429", "cooldown starts and ends are logged".

### AC6

**Mark:** document

**Scenario:** Reviewer checks the diff: `services/breaker.py`, `services/quota.py`,
`adapters/openai_compat.py`, `tests/core/test_breaker.py`, `tests/core/test_quota.py` and
`tests/core/test_openai_compat.py` are gone; the timeout-blame tests in `test_gateway.py` are gone; every
other gateway test listed under AC1–AC5 passes; LLD "6.6 Circuit breaker" and "6.8 Free quota" describe the
LiteLLM cooldown and no header quota.

### AC7

**Mark:** unit test

**Scenario:** Given the socket layer patched to fail every connection, when the LiteLLM adapter is imported
and a Router is built from `config/models.yaml`, then no connection or DNS lookup is attempted. `uv.lock`
pins `litellm` to one exact version.

**Planned test:** unit, `tests/core/test_litellm_router.py`, "imports and builds without network".

## Contract

### POST /api/v1/ask · altered

| Item | Before | After |
| --- | --- | --- |
| `degraded.reason` | `quota_exhausted`: no `answer` entry within its provider's header-reported quota | `quota_exhausted`: every free `answer` entry in cooldown after a 429 while the paid entry can't run |

`budget_exhausted` and `models_unavailable` keep their meaning. No status, error or schema changes.

### GET /api/healthz · altered

| Item | Before | After |
| --- | --- | --- |
| `mode: degraded` | Budget at the cap, every `answer` entry's breaker open, or no `answer` entry within quota | Budget at the cap, or every free `answer` entry in cooldown while the day is past the paid cutoff |

No table changes. `log_events` gets `cooldown_started` and `cooldown_ended` events (component `gateway`) in
place of the breaker transitions.

**Config files:** `config/models.yaml` and `config/models.local.yaml` keep their shape, minus
`providers.*.daily_requests`. A provider gains `kind`: `openrouter` (default for the OpenRouter base URL, so
its reported cost is read) or `openai` (any other OpenAI-compatible endpoint: Groq, NVIDIA, Google, Ollama).
The `rerank` stage's entries are called as a Cohere-style `/rerank` on the provider's base URL.

**Rule values (`limits.yaml`):** `quota_warn`, `breaker_failures` and `breaker_window` are removed;
`breaker_cooldown` stays as the Router's cooldown when the provider sends no `Retry-After`.

**Dependency:** `litellm` at an exact version (1.108.5, LTS line, published 2026-11-10 and matching the
GitHub release).

## Contract verification

- `degraded` with `models_unavailable` and `quota_exhausted` from cooldowns, no model call: AC5.
- `degraded` with `budget_exhausted`: unchanged code path, existing test "budget at the cap answers search-only
  with no model call" (kept).
- `GET /api/healthz` `mode: degraded` from cooldowns: AC5.
- `log_events` `cooldown_started` and `cooldown_ended` rows: AC5.
- `budget_daily` reservation and settle for the paid entry: AC2.
- Remote rerank through the Router, falling to local: AC4.
- `models.yaml` without `daily_requests`, with `kind`: AC7 (the Router builds from the shipped file) and the
  existing "the shipped files load".

## Decisions

- **Router scope:** one `litellm.Router` in the API process, built inside the running event loop (its budget
  sync task needs one). One model group per stage, holding that stage's free entries as deployments with
  `order` = chain position, so it tries them in order instead of load-balancing. Reason: the owner's rule
  (use a maintained library where it covers the need); order, cooldown, timeouts and pre-first-chunk stream
  fallback are LiteLLM's.
- **No retries in the Router:** `num_retries=0`. The next entry is the retry; with the default (2), a 429 with
  `Retry-After: 30` would hold a question for about a minute before falling through.
- **Cooldown:** LiteLLM's defaults (a 429 at once, more than half the calls failing in a minute with at least
  five, a timeout, a 5xx, a 401/404), for `Retry-After` when sent, otherwise `BREAKER_COOLDOWN`, set at Router
  level so `Retry-After` wins. Owner's decision: no half-open trial, no timeout-blame rule. A stage with one
  free entry (rerank today) gets no 429 cooldown (LiteLLM exempts single-deployment groups); each question
  then tries it once and falls to local, which costs one fast 429.
- **Paid entry outside the Router:** owner's decision. When the free group raises, the gateway reserves the
  estimate in Postgres and calls the paid entry through `litellm.acompletion` (same library, no Router). The
  Router's own budget checks only spend already recorded, so concurrent calls could pass the cap together; it
  is not used. Settle: refused → 0; reported cost (`response_cost`, from OpenRouter's `usage.cost`) when
  present; otherwise the usage at the configured price; no usage → the estimate.
- **No header quota:** owner's decision. Free limits are handled by the provider's 429 and the cooldown;
  `daily_requests` and `QUOTA_WARN` go.
- **Deadline and request cap stay ours:** LiteLLM has no total deadline. Each call runs under
  `asyncio.timeout_at(deadline)` and passes `timeout=min(TIMEOUT_<STAGE>, time left)`; the rerank group keeps
  one `TIMEOUT_RERANK` for the whole group.
- **Model misses:** an empty answer or a schema miss is retried once through the Router (it may land on the
  same entry); a second miss falls to the paid entry. LiteLLM sees these as successes, so they never cool an
  entry.
- **Reading cooldowns:** LiteLLM's cooldown callback only feeds Prometheus, so the gateway reads the active
  cooldowns itself (`async_get_cooldown_deployments_with_debug_info`: deployment, status, start, length)
  after each Router call and in `mode()`, and compares with the set it saw last: a new one writes
  `cooldown_started`, one gone writes `cooldown_ended` (at the time it is noticed).
- **`mode()` and alerts:** with every free `answer` deployment in cooldown and the paid entry unable to run
  (day past the cutoff, or no paid entry), the reason is `quota_exhausted` when every one was cooled by a 429,
  otherwise `models_unavailable`; the SSE contract doesn't change. `providers_down` opens when every free
  `answer` deployment is in cooldown and closes when one is back. `budget_exhausted` is unchanged.
- **No network at import:** the adapter sets `LITELLM_LOCAL_MODEL_COST_MAP=True` before importing `litellm`
  (otherwise the import fetches the cost map from GitHub), and the API image sets it too.
- **Supply chain:** exact pin, a release at least two weeks old with a matching GitHub release (versions
  1.82.7 and 1.82.8 were malicious, March 2026); upgrades are reviewed PRs.
- **Where LiteLLM lives:** only in `rag_core/adapters` (import-linter forbids it elsewhere, like `httpx`).
- **Test server:** LiteLLM builds its own HTTP clients, so the fake provider becomes a small ASGI app served on
  `127.0.0.1` during the tests (same scripted behaviors as `tests/fakes/openrouter.py`), and the chains point
  their `base_url` at it.
- **Local profile:** Ollama through `kind: openai` at `http://localhost:11434/v1`; the compose address stays an
  open question in the LLD (F01-T06).

## Files and interfaces

- `packages/core/src/rag_core/adapters/litellm_router.py` (new): `LiteLLMModels` builds the Router from
  `ModelsConfig`; `complete(stage, messages, *, schema, timeout)`, `stream(stage, messages, *, timeout)`,
  `rerank(query, documents, *, timeout)`, `complete_paid(entry, messages, ...)`, `stream_paid(...)`,
  `cooldowns() -> dict[str, Cooldown]` (deployment → status, start, length), `close()`.
- `packages/core/src/rag_core/ports/__init__.py`: `ModelProvider` replaced by `ModelRouter` (the operations
  above); `ChatResult`, `ChatChunk`, `RerankResult` keep their shapes, plus `cost_usd: float | None`;
  `RateInfo` removed.
- `packages/core/src/rag_core/services/gateway.py`: keeps `complete`, `stream`, `rerank`, `mode`, `drain`,
  `close` and their signatures; drops breakers, quota and the blame rule; keeps the paid reserve/settle, the
  deadline, the request cap, the schema retry and the alerts.
- `packages/core/src/rag_core/services/breaker.py`, `services/quota.py`, `adapters/openai_compat.py`: removed.
- `packages/core/src/rag_core/models_config.py`: `daily_requests` removed, `kind` added.
- `packages/core/src/rag_core/adapters/factory.py`: `build_gateway` builds `LiteLLMModels`.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: the three removed values.
- `config/models.yaml`, `config/models.local.yaml`: `daily_requests` out, `kind` in.
- `packages/core/pyproject.toml`, `uv.lock`: `litellm==1.108.5`; `pyproject.toml`: import-linter forbids
  `litellm` outside adapters.
- `infra/Dockerfile` (API target): `ENV LITELLM_LOCAL_MODEL_COST_MAP=True`.
- `tests/fakes/openrouter.py`: served as an ASGI app on a local port; `tests/conftest.py` starts it.

## Existing tests that change

- `tests/core/test_breaker.py`, `tests/core/test_quota.py`, `tests/core/test_openai_compat.py`: removed with
  their modules.
- `tests/core/test_gateway.py`: the timeout-blame, keep-alive, half-open and quota tests are removed; the
  chain, paid, stream, deadline, rerank and mode tests are kept and pointed at the local fake server; new ones
  per AC1, AC2, AC5.
- `tests/api/test_degraded.py`: providers down is driven by cooldowns instead of breakers.
- `tests/api/test_ask.py`: `test_rerank_goes_local_past_the_quota_warning` removed (no quota rule).
- `tests/contract/test_rerank_live.py`: calls through the Router.
- `tests/conftest.py`: the fake server fixture.

## Data change

None.

## Updated documents

- `docs/adr/0016-litellm-router-behind-the-model-gateway.md` (new).
- `docs/lld.md`, "1. Code structure" (library table: model clients), "Ports" (ModelGateway guarantees,
  `ModelRouter` in place of the provider port), "Logs", "Configuration" (models config), "6.2 Daily budget"
  (paid entry outside the Router, settle from the reported cost), "6.6 Circuit breaker" (LiteLLM cooldown),
  "6.8 Free quota" (no header quota), "6.10 Alerts" (`providers_down` from cooldowns), "9. Values and
  configuration" (removed values), "10. Development environment" (fake server), "11. Decisions" (ADR 0016).

# Plan for F01-T04: Gate

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** HTTP test

**Scenario:** Given the API built with Turnstile's always-pass test secret, when a visitor asks with the
dummy token, then the stream opens. Given the always-fail test secret, or a token the provider rejects, then
the answer is `403 bot_check_failed` with the catalog body, and no rate-limit token is taken and no search
runs. Given a provider that errors, times out or answers something other than JSON, then the answer is also
`403 bot_check_failed` (fail closed). Given a body with an empty or missing `turnstile`, then
`422 invalid_input` (shape check, before the bot check).

**Planned test:** e2e of the API, `tests/api/test_gate.py`, "passes with a valid token", "refuses a token
the provider rejects", "refuses when the provider errors", "refuses when the provider times out", "checks
the token before the rate limit".

### AC2

**Mark:** HTTP test

**Scenario:** Given a question with NFKC-variant characters (full-width letters), control characters and
bidi or zero-width format characters, when it is asked, then the search receives the normalized text
(visible in the `retrieval` hits matching the plain question). Given a question of `MAX_QUESTION_CHARS`
characters after normalization, then the stream opens; with one character more, or a question that is only
whitespace and control characters, then `422 invalid_input`.

**Planned test:** unit, `tests/core/test_question.py`, "normalizes NFKC and strips control and format
characters", "caps the length after normalization"; e2e of the API, `tests/api/test_gate.py`, "refuses a
question over the cap".

### AC3

**Mark:** HTTP test, integration test

**Scenario:** Given `RATE_IP_BURST` questions from one IP within `RATE_IP_WINDOW`, when the next one
arrives, then it gets `429 rate_limited` with `Retry-After` in whole seconds until one token refills; the
same for one session from different IPs (`RATE_SESSION_BURST`). Given the daily bucket empty, then
`Retry-After` counts to the next UTC midnight. Given a refused request, then no other bucket loses a token.
Given a bucket with one token left and several concurrent takes against the real database, then exactly
one passes. The test API uses a limits file with small bursts so the tests stay short.

**Planned test:** e2e of the API, `tests/api/test_gate.py`, "limits questions per IP", "limits questions
per session", "says when the daily limit resets", "a refused question takes no token"; integration,
`tests/core/test_rate_limit.py`, "concurrent takes never pass more than the bucket holds".

### AC4

**Mark:** HTTP test, human review

**Scenario:** Given a question from `CloudFront-Viewer-Address: 203.0.113.7:4431`, when it is asked, then
no `rate_buckets` key and no captured log line (app logs and the request line) contains `203.0.113.7`, and
the keys hold the HMAC of that day; the next UTC day the same IP gets a different key. Human review: in
the diff, the raw IP lives only in the function that hashes it; uvicorn's access log is off; no log call
receives the IP, the token or the question before scrubbing.

**Planned test:** e2e of the API, `tests/api/test_gate.py`, "never stores or logs the raw IP"; unit,
`tests/core/test_client_ip.py`, "the hash changes with the UTC day".

### AC5

**Mark:** Playwright test

**Scenario:** In zorzo-site, with the Turnstile script replaced by a stub that hands out tokens: given
`POST /api/v1/ask` answering `422`, then an inline message shows under the box; given `403` once, then the
page gets a new token and asks again, and the stream shows; given `403` twice, then the bot-check message
shows; given `429` with `Retry-After: 300`, then the page says when to try again (in five minutes); given
`503`, then offline (F01-T03). Given the stub failing to produce a token, then the bot-check message shows
and no request is sent. Every request carries the stub's token, and each question gets a new one.

**Planned test:** Playwright, `tests/e2e/gate.spec.ts`, "says what to fix on 422", "retries the bot check
once on 403", "gives up after a second 403", "says when to try again on 429", "says the bot check failed
without a token", "sends a fresh token with each question".

### AC6

**Mark:** HTTP test

**Scenario:** Given the API suite, then the bot check runs with Turnstile's documented test secrets
(`1x0000000000000000000000000000000AA` always passes, `2x0000000000000000000000000000000AA` always fails)
and the dummy token; one test marked `network` calls the real `siteverify` with both secrets, proving the
suite's stand-in answers as Cloudflare does.

**Planned test:** e2e of the API, `tests/api/test_gate.py` (the AC1 cases);
`tests/contract/test_turnstile_live.py`, "the test secrets behave as documented" (marked `network`).

### AC7

**Mark:** HTTP test

**Scenario:** Given `GET /api/v2/nothing`, then `404 not_found` with the catalog body and a `trace_id`;
given `GET /api/v1/ask`, then `405 method_not_allowed` with the catalog body, a `trace_id` and the `Allow`
header.

**Planned test:** e2e of the API, `tests/api/test_errors.py`, "unknown path is not_found", "wrong method
is method_not_allowed".

## Contract

### POST /api/v1/ask · altered

| Item | Before | After |
| --- | --- | --- |
| Authentication | Public; `turnstile` only non-empty | Public; `turnstile` verified with Turnstile `siteverify` before anything else |
| Request headers | — | `CloudFront-Viewer-Address` (`ip:port`), set by CloudFront; required in production, the socket peer locally |
| Body | `q` trimmed, 1 to `MAX_QUESTION_CHARS` | `q` after NFKC, control and format characters removed, whitespace collapsed, trimmed: 1 to `MAX_QUESTION_CHARS` code points |
| Response headers | — | `Retry-After` (whole seconds) on `429` |
| Errors | `422 invalid_input`, `503 unavailable`, `500 internal` | Adds `403 bot_check_failed` (token rejected, provider error or timeout) and `429 rate_limited` (an IP or session bucket is empty); `503 unavailable` also when production gets no viewer address |

Order before the stream: body cap → body shape (`422`) → bot check (`403`) → question normalization
(`422`) → rate limit (`429`) → corpus check (`503`) → stream.

### Any path · altered

| Item | Before | After |
| --- | --- | --- |
| Errors | Starlette's `{"detail": …}` for 404 and 405 | `404 not_found` and `405 method_not_allowed` with the catalog body and `trace_id`; 405 keeps `Allow` |

No table changes: `rate_buckets` (`key`, `tokens`, `refilled_at`) exists.

**Settings (API):** `TURNSTILE_SECRET` and `SALT_SEED` become required (`SecretStr`, never logged).
**Rule values (`limits.yaml`):** `rate_ip_burst`, `rate_ip_window`, `rate_ip_daily`, `rate_session_burst`,
`rate_session_daily`, `timeout_turnstile`.

## Contract verification

- `POST /api/v1/ask`, `200` after a passing gate: AC1.
- `403 bot_check_failed` (rejected token, provider error, timeout): AC1, AC6.
- `422 invalid_input` (missing token, question over the cap, empty after normalization): AC1, AC2.
- `429 rate_limited` with `Retry-After`, per IP, per session, daily: AC3.
- `503 unavailable` without a viewer address in production: AC4's test file, "refuses without a viewer
  address in production" (added to `test_gate.py`; no criterion names it, it guards the fail-closed rule).
- `404 not_found`, `405 method_not_allowed`: AC7.
- `rate_buckets` concurrency (one upsert, no double pass): AC3.

## Decisions

- **Gate module:** `rag_core.services.gate` runs bot check, normalization and rate limit in that order,
  raising `ApiError`; the route calls it before the corpus check. Budget joins it in F01-T05.
- **Bot check port:** `BotCheck` with `verify(token) -> bool`; the Turnstile adapter posts `secret` and
  `response` to `siteverify` with httpx (explicit core dependency) and reads `success` only. `remoteip` is
  not sent (the raw IP stays inside the app) and `hostname` is not checked (Cloudflare ties a site key to
  its hostnames; test keys answer `example.com`). No retry: `internal-error`, a timeout or a non-JSON answer
  refuse at once, as LLD "Security" says a provider error does.
- **Rate limit, all or nothing:** the four buckets (IP burst, IP day, session burst, session day) are taken
  in one transaction by one multi-row `INSERT … ON CONFLICT DO UPDATE … WHERE tokens ≥ 1`; if fewer rows
  come back than buckets, the transaction rolls back, so a refused request takes nothing. Rows go in fixed
  key order, so concurrent requests can't deadlock. Burst buckets refill continuously (`BURST` per
  `WINDOW`); day buckets have the UTC date in the key and don't refill. `Retry-After` is the largest wait
  among the empty buckets.
- **Keys:** `ip:<hmac>:burst`, `ip:<hmac>:<date>`, `session:<uuid>:burst`, `session:<uuid>:<date>`, with
  `hmac = HMAC-SHA256(daily_salt, ip)` and `daily_salt = HMAC-SHA256(SALT_SEED, utc_date)` (LLD "Security").
- **Normalization:** NFKC; characters of Unicode categories `Cc` and `Cf` removed (`Cf` holds the zero-width
  and bidi controls that can hide text from a reader but not from a model), with tab and line breaks
  turned into spaces first; whitespace runs collapsed; trimmed; then the cap, in code points.
- **Logs:** a refused request logs one `warning` (`bot_check_failed` with Cloudflare's error codes, or
  `rate_limited` with the bucket kind), never the key, token or IP. Uvicorn runs with `access_log=False`
  (its line holds the raw socket address) and `proxy_headers=False` (so `X-Forwarded-For` changes nothing).
  The `log_events` row for a rate limit comes with the TraceSink (F01-T10).
- **Front, token per question:** `ask(question, {signal, token})` in `data/api.js` takes the token from
  `ui/turnstile.js` (widget in `execution: "execute"` mode, invisible); after each use the widget is reset
  and the next token is fetched in the background, so a question rarely waits for one. `AskError` gains
  `invalid` (422), `bot` (403) and `limited` (429, with `retryAfter`); `ui/ask.js` retries a `bot` once
  with a new token. The placeholder `"unverified"` goes away; `tools/record_streams.py` sends the dummy
  token to an API run with the always-pass test secret.
- **Turnstile in the API suite:** the suite's `siteverify` is an httpx `MockTransport` answering as
  Cloudflare documents the test secrets (and erroring or hanging on demand), so the suite stays offline and
  fast; one `network` test calls the real endpoint with both test secrets, run in full verification.
  Rejected: every gate test calling Cloudflare (slow, needs the network in CI, and the provider error can't
  be provoked).
- **`TIMEOUT_TURNSTILE` = 3 s:** Cloudflare sets no value (its examples use 10 s); 3 s, the same order as
  `TIMEOUT_ROUTE`, so a slow provider costs the visitor a few seconds before a 403 rather than most of
  `REQUEST_DEADLINE`.
- **Viewer IP:** `CloudFront-Viewer-Address` (`ip:port`; CloudFront sets it and the origin secret keeps
  others out); without it, the socket peer when `APP_ENV=local`, and `503 unavailable` plus an `error` log
  in production, since behind Caddy every visitor would share one bucket. Closes the LLD open question.
- **IPv6:** the `/64` prefix is hashed (one subscriber usually holds a whole `/64`, so the full address
  would let one visitor rotate past the limit); IPv4 is hashed whole.
- **Turnstile script, the one third-party load:** Cloudflare forbids self-hosting or proxying it, so
  zorzo-site's LLD and `AGENTS.md` record the exception. The script loads on the first interaction with the
  question box (focus, typing or an example click), not on page load, so a visitor who only browses never
  contacts Cloudflare.
- **No token in the browser** (script blocked, widget error or timeout): the same bot-check message as a
  second `403`, with no request sent; a new row in LLD "Front against the API".
- **Site key per environment, no build step:** the production site key in the front's config, and the
  invisible always-pass test key (`1x00000000000000000000BB`) on a local host, the same host check that
  picks `demo-data/`.

## Files and interfaces

zorzo-api:

- `packages/core/src/rag_core/services/gate.py` (new): `check(request_in, client_ip, …) -> str` (the
  normalized question) or `ApiError`.
- `packages/core/src/rag_core/ports/__init__.py`: `BotCheck`, `RateLimiter`.
- `packages/core/src/rag_core/adapters/turnstile.py` (new): `TurnstileBotCheck(secret, timeout, transport=None)`.
- `packages/core/src/rag_core/adapters/rate_limit.py` (new): `PostgresRateLimiter(db).take(buckets) ->
  float | None` (seconds to wait, or `None` when it passes).
- `packages/core/src/rag_core/services/client_ip.py` (new): `viewer_ip(headers, peer, app_env)`,
  `hashed(ip, seed, day)`.
- `packages/core/src/rag_core/services/question.py`: `normalize` with NFKC and character removal.
- `packages/core/src/rag_core/errors.py`: `NOT_FOUND`, `METHOD_NOT_ALLOWED`.
- `packages/core/src/rag_core/limits.py`, `config/limits.yaml`: the rate and timeout values.
- `packages/core/pyproject.toml`: `httpx`.
- `apps/api/src/rag_api/settings.py`: `turnstile_secret`, `salt_seed`.
- `apps/api/src/rag_api/ask.py`: calls the gate. `apps/api/src/rag_api/errors.py`: 404 and 405 handler.
  `apps/api/src/rag_api/main.py`: builds the bot check and the limiter. `apps/api/src/rag_api/__main__.py`:
  uvicorn flags.
- `infra/compose.yaml`, `infra/.env.example`: test secret and a local salt seed.
- `tests/api/test_gate.py`, `tests/core/test_question.py`, `tests/core/test_client_ip.py`,
  `tests/core/test_rate_limit.py`, `tests/contract/test_turnstile_live.py` (new); `tests/conftest.py`
  (`api_settings` with the test secret and seed, the `siteverify` stand-in); `pyproject.toml` (`network`
  marker).

zorzo-site:

- `assets/js/ui/turnstile.js` (new): `startTurnstile({siteKey})` → `{token(): Promise<string>, used()}`.
- `assets/js/data/api.js`: `ask(question, {signal, token})`, new `AskError` kinds.
- `assets/js/ui/ask.js`: 422, 403 retry, 429 and no-token reactions. `assets/js/main.js`: starts Turnstile.
- `assets/js/i18n.js`: `invalidInput`, `botCheck`, `tryAgainIn(seconds)` (PT, EN).
- `index.html`, `assets/styles.css`: the widget's container and the inline message.
- `tools/record_streams.py`: the dummy token. `tests/e2e/fixtures.ts`: the Turnstile stub and `serveError`.
- `tests/e2e/gate.spec.ts` (new). `docs/lld.md`, `AGENTS.md`.

## Existing tests that change

- zorzo-api `tests/api/test_ask.py` and the others that post a question: they send the dummy token and the
  app is built with the always-pass secret; behavior unchanged.
- zorzo-api `tests/api/test_errors.py`: gains the 404 and 405 cases.
- zorzo-site `tests/e2e/ask.spec.ts`, "asks the API and reads the stream": `turnstile` is the stub's token
  instead of any non-empty value. The auto fixture serves the stub to every test.

## Data change

None.

## Updated documents

- `docs/lld.md`, "Security": viewer IP header and the production refusal without it; IPv6 `/64`; `Cc` and
  `Cf` removal; the bot check's fail-closed cases.
- `docs/lld.md`, "6.7 Rate limit": all-or-nothing take, keys, refill, `Retry-After`.
- `docs/lld.md`, "Concurrency and idempotency": the rate limit's multi-row upsert.
- `docs/lld.md`, "6.11 Retention": `rate_buckets` rows idle for more than a day are deleted.
- `docs/lld.md`, "Front against the API": token per question with background refresh; the no-token row.
- `docs/lld.md`, "Values and configuration": `TIMEOUT_TURNSTILE`.
- `docs/lld.md`, "Open questions": the viewer IP header closed.
- zorzo-site `docs/lld.md` ("Stack": the Turnstile exception; "Modules") and `AGENTS.md` change with the code.

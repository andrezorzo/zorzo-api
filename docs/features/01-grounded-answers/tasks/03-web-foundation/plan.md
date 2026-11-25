# Plan for F01-T03: Web foundation (zorzo-site)

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** Playwright test

**Scenario:** Given the page with the published fixture data and `POST /api/v1/ask` answered by a recorded
search-only stream, when a visitor asks a question, then the browser sends one `POST /api/v1/ask` with
`fetch` (JSON body with `q`, `session` and `turnstile`), the steps panel shows the stream's stages and the
answer panel shows the stream's passages. Given a first question whose stream is still open, when the
visitor asks a second one, then the first request is aborted and only the second question's passages show.

**Planned test:** Playwright, `tests/e2e/ask.spec.ts`, "asks the API and reads the stream", "a new question
cancels the current one".

### AC2

**Mark:** Playwright test

**Scenario:** Given the fixture's example questions, when a visitor opens the page in English, then the
English examples show as clickable questions next to the question box; when the visitor switches to
Portuguese, the Portuguese examples replace them; when the visitor clicks one, that question is asked
(it goes to the box and a `POST /api/v1/ask` carries it as `q`).

**Planned test:** Playwright, `tests/e2e/data.spec.ts`, "shows the example questions of the interface
language" (extended from F01-T12), and `tests/e2e/ask.spec.ts`, "clicking an example asks it".

### AC3

**Mark:** Playwright test

**Scenario:** Given a tab with no session, when the visitor asks a question, then the request's `session` is
a UUID and the same value is in the tab's `sessionStorage`; when the visitor reloads the page and asks
again, then the request carries the same UUID.

**Planned test:** Playwright, `tests/e2e/ask.spec.ts`, "keeps the conversation id across a reload".

### AC4

**Mark:** Playwright test

**Scenario:** Given `GET /api/healthz` answering `503 {status: down}`, when the page loads, then the question
box is disabled with the offline notice; when health answers `200 {status: ok, mode: normal}` and
`HEALTH_POLL_INTERVAL` passes (the test drives the clock), then the box is enabled again with no reload.
Given health answering `mode: degraded`, the page shows the search-only notice before any question. Given a
question whose request fails at the network or answers `503`, the page goes offline and recovers the same
way.

**Planned test:** Playwright, `tests/e2e/health.spec.ts`, "goes offline and recovers by itself", "shows the
degraded notice from health", "a failed request goes offline".

### AC5

**Mark:** Playwright test

**Scenario:** Given a recorded stream with `degraded` and six hits, when a visitor asks, then the answer
panel shows the search-only notice and six passages, each with its source title and, when the chunk has
one, its link. Given a recorded stream with `degraded` and no hits, then the panel shows "no matching
sources for this question" and the example questions of the interface language.

**Planned test:** Playwright, `tests/e2e/ask.spec.ts`, "shows the passages of a search-only answer", "shows
the examples when nothing matches".

### AC6

**Mark:** Playwright test

**Scenario:** Given a browser without WebGL, or with `prefers-reduced-motion: reduce`, or whose WebGL context
is lost mid-session, when a visitor asks a question answered by a recorded stream, then the still image
shows and the steps panel and the passages show as with the brain.

**Planned test:** Playwright, `tests/e2e/fallback.spec.ts`, "asks without WebGL", "asks with reduced motion",
"falls back without 3D and keeps answering" (F01-T12's tests, now against recorded streams).

### AC7

**Mark:** Playwright test

**Scenario:** Given the recorded streams in `tests/fixtures/streams/` and the copy of
`contracts/sse-v1.schema.json` next to them, when the suite runs, then every event of every stream
validates against the schema, and the stream every test serves to the page is one of those files.

**Planned test:** Playwright (no browser), `tests/e2e/streams.spec.ts`, "every recorded stream validates
against the SSE schema".

## Contract

The contract doesn't change: the front starts consuming `POST /api/v1/ask`, `GET /api/healthz` and the
stream events of LLD "HTTP routes" and "Stream events" (owned by 02) as they are.

## Contract verification

- `POST /api/v1/ask`, `200 text/event-stream` with `stage`, `degraded`, `retrieval`, `done`: AC1, AC5, AC7.
- `POST /api/v1/ask`, `503` and network failure: AC4. The `422`, `403` and `429` reactions are F01-T04 (AC5
  there).
- `error` event (followed by `done`) and a stream dropped before `done`: AC7 (the recorded error stream
  validates); their reactions are covered by a case in `ask.spec.ts`, "says when the stream fails".
- `ping` comments: AC1 (the recorded streams keep them, so the reader must skip them).
- `GET /api/healthz`, `200` with `mode` `normal` and `degraded`, and `503`: AC4.
- `retrieval` ids match `corpus.json` of the same version: AC5 (the fixture data and the recorded streams
  come from the same database).

## Decisions

- **Transport:** `assets/js/data/api.js` exports `ask(question, {signal})`, the same async iterator the
  interface already consumes, now reading `POST /api/v1/ask` with `fetch` and a small SSE reader over the
  response body (`event:`/`data:` frames, `: ping` comments skipped, a frame split across chunks
  reassembled). No library: the format is a few lines and nothing may load from another origin. Each
  frame becomes `{kind, ...data}`, the shape the mock produced, so `ui/ask.js` keeps its event switch.
- **Relative URLs:** the front calls `/api/v1/ask` and `/api/healthz` on its own origin, as in production
  (ADR 0003). No API base URL setting.
- **Errors split between 03 and 04:** this task handles `503` and network failure (offline), a stream
  dropped before `done` ("Connection lost, ask again"), the `error` event (generic message with the trace
  link) and `degraded`. Any other status shows the generic message until F01-T04 adds the `422`, `403` and
  `429` reactions.
- **Turnstile before the gate:** the request sends a fixed placeholder token (`"unverified"`): the API only
  checks that it is non-empty until F01-T04, which adds the widget and the fresh token per question. The
  token comes from one function, so 04 changes one place.
- **Conversation id:** LLD "Front against the API", "Session": a UUID from `crypto.randomUUID()`, created on
  the first question and kept in `sessionStorage` under one key. The "new conversation" button and the
  `SESSION_TTL` reset are F01-T07 (AC5 there).
- **Health:** `ui/health.js` checks `GET /api/healthz` on load; offline (non-200 or network failure)
  disables the box and shows the notice, then checks every `HEALTH_POLL_INTERVAL` and when the tab regains
  focus; `mode: degraded` shows the search-only notice. A failed question (network or `503`) also sets
  offline. Health is checked only on load, while offline and after a failure: no polling while online.
- **A hit the loaded corpus doesn't know** (a new corpus version activated after the page loaded) still
  shows as a passage by its `title` from the stream, with no text, link or brain point; today such a hit is
  dropped, which would turn real matches into "no matching sources".
- **Recorded streams:** recorded from the real API in search-only mode, running with `EMBEDDER=fake
  APP_ENV=local` over the same database whose publish produced `tests/fixtures/data/`, so the hit ids match
  the fixture corpus: one stream with passages, one with no hits, saved byte for byte with their `ping`
  comments. The `error` stream can't be provoked from outside the API, so it is written by hand and is
  valid only because AC7 validates it; the dropped stream is the passages stream cut before `done`, built in
  the test. `tools/record_streams.py` posts the questions to a running local API and writes the files and
  the schema copy; `AGENTS.md` gets the command next to "Refresh the test fixture".
- **Schema check:** `ajv` (pinned, dev dependency only) validates every recorded event against the copy of
  `sse-v1.schema.json` in `tests/fixtures/`, refreshed with the streams.
- **Mock removed with its guard:** `data/mock.js` and its BM25 search go; the `blocked` rendering stays for
  F01-T08. Locally, the page asks the API run by zorzo-api's compose.
- **Local page and API:** `tools/serve.py` (Python standard library) replaces `npm run serve`: it serves the repository as today and forwards `/api/*` to
  `http://127.0.0.1:8000`, streaming the response, so the page keeps one origin locally as CloudFront gives
  it in production. Rejected: CORS on the API for localhost (LLD "Security" allows it, but the front would
  then need an API base URL that production doesn't have).
- **Example questions (AC2):** today they are the box's rotating placeholder and the list after "no matching
  sources". Now also a short list of clickable questions under the box,
  in the `.ans-examples` style, shown while no answer is open and hidden while one is (on a phone too); the
  rotating placeholder stays. Screenshots at 1440 and 390 go in this task's folder for review.
- **No local stand-in for the API:** without the API the page is offline (AC4),
  while the brain still draws the demo data. Rejected: keeping the mock as a local-only fallback, a second
  answer path in the shipped code that tests would have to cover.

## Files and interfaces

zorzo-site:

- `assets/js/data/api.js` (new): `ask(question, {signal})`, async iterator of `{kind, ...data}`;
  `health()` → `{state: "online" | "degraded" | "offline"}`; `sessionId()`.
- `assets/js/data/mock.js`: removed.
- `assets/js/ui/health.js` (new): `startHealth({onState})`, the states and the polling.
- `assets/js/ui/ask.js`: the examples list, offline and degraded notices, the connection-lost and error
  messages with the trace link, hits unknown to the corpus; the `blocked` branch stays.
- `assets/js/main.js`: imports `ask` from `data/api.js`, starts health.
- `assets/js/i18n.js`: offline, degraded, connection-lost and trace-link texts (PT, EN).
- `index.html`, `assets/styles.css`: the examples list and the notices.
- `tools/serve.py` (new), `tools/record_streams.py` (new);
  `package.json` (`serve`, `ajv`), `package-lock.json`, `playwright.config.ts` (web server command).
- `tests/fixtures/streams/*.sse`, `tests/fixtures/sse-v1.schema.json` (new); `tests/e2e/fixtures.ts`
  (`serveStream(page, name)`, `serveHealth(page, response)`); `tests/e2e/ask.spec.ts`,
  `tests/e2e/health.spec.ts`, `tests/e2e/streams.spec.ts` (new).
- `docs/lld.md` (sections "Modules", "Development data and tests"), `AGENTS.md` (commands).

## Existing tests that change

- `tests/e2e/fallback.spec.ts` and `tests/e2e/data.spec.ts`: they asked the mock; they now get a recorded
  stream and a healthy `/api/healthz` through request interception. Same assertions, plus the examples
  list in "shows the example questions of the interface language".
- `tests/e2e/data.spec.ts`, "keeps the question box when the published data fails": the box stays enabled
  only while health is online, so the test serves a healthy `/api/healthz`.

## Data change

None.

## Updated documents

- None in zorzo-api: the front's behavior against the API (LLD "Front against the API") is already the rule
  this task implements. zorzo-site's `docs/lld.md` ("Modules": the API client replaces the mock;
  "Development data and tests": the local proxy and the recorded streams) changes with the code.

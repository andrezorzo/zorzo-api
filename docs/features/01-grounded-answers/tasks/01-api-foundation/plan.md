# Plan for F01-T01: API foundation

Task: [task.md](task.md) · Spec: [spec.md](../../spec.md) · PRD: [prd.md](../../prd.md)

## Criteria

### AC1

**Mark:** HTTP test

**Scenario:** Given the API running against a reachable database, when a client calls the health endpoint,
then it gets `200` with `{status: ok, mode: normal, db: ok}`; given today's budget row marked degraded, then
`mode: degraded`; given an unreachable database, then it gets `503` with `{status: down, mode: unknown,
db: down}`. The response is never cached.

**Planned test:** API e2e, `tests/api/test_healthz.py`, "reports ok with the database up", "reports
degraded when today's budget is exhausted", "reports down when the database is unreachable".

### AC2

**Mark:** integration test

**Scenario:** Given the workspace, when the import contracts run, then `core` imports neither `apps` nor
`domains`, and nothing outside `core` adapters imports a network client. Given `DOMAIN=zorzo`, when the API
starts, then it loads `domains/zorzo`; given `DOMAIN` naming a missing package, then startup fails before
serving.

**Planned test:** integration, `tests/test_architecture.py`, "import contracts hold" (runs the import
contracts); `tests/api/test_startup.py`, "loads the domain chosen by DOMAIN", "refuses a missing domain".

### AC3

**Mark:** integration test

**Scenario:** Given an empty database, when the migrations run, then every table in the contract below
exists with its constraints; inserting an outcome outside the six values is rejected; a second `active`
corpus version is rejected; and the API, after reflecting the schema, sees the same tables and columns
without declaring any of them.

**Planned test:** integration, `tests/schema/test_migrations.py`, "creates every F01 table", "rejects an
unknown outcome", "allows one active corpus version", "API reflection matches the migrations".

### AC4

**Mark:** integration test

**Scenario:** Given `DATABASE_URL` unset, or the domain config file invalid, when the API process starts,
then it exits with an error naming the problem and never accepts a request.

**Planned test:** integration, `tests/api/test_startup.py`, "stops without DATABASE_URL", "stops on an
invalid domain config".

### AC5

**Mark:** HTTP test

**Scenario:** Given a test-only route mounted in the test app, when it receives a body missing a field,
then the response is `422` with `{"error": {"code": "invalid_input", "message", "trace_id"}}`; when it
raises an unexpected exception, then `500` with code `internal`; when it raises the shared "unavailable"
error, then `503` with code `unavailable`. Every response carries a `trace_id` that also appears in the
request's log line.

**Planned test:** API e2e, `tests/api/test_errors.py`, "maps validation errors to invalid_input", "maps
unexpected errors to internal", "maps unavailable to 503", "error trace_id matches the log line".

### AC6

**Mark:** human review

**Scenario:** The reviewer opens the CI workflow and a run on the task's PR, and finds ruff, mypy, the
import contracts and pytest running against a Postgres service with pgvector, all green; and the test
fixture that creates one database per test session, applies the Django migrations and drops it at the end.

## Contract

### GET /api/healthz · new

| Item | Description |
| --- | --- |
| Authentication | Public; no gate |
| Path and query parameters | None |
| Request body | None |
| Input headers | None |
| Success response | `200` `{status: "ok", mode: "normal" \| "degraded", db: "ok"}` |
| Response headers | `Cache-Control: no-store` |
| Errors | `503` `{status: "down", mode: "unknown", db: "down"}` when the database can't be reached (same shape as success, not the error body: monitors read it) |

### Tables · database `zorzo` · new

All times `timestamptz`; ids `bigint` identity unless stated. `CHECK` constraints are Django
`CheckConstraint`s; the `vector` extension is created by a hand-written `RunSQL` step in the first
migration.

**`corpus_versions`**
- `version` bigint pk; `status` text not null default `candidate`, check in (`candidate`, `active`,
  `retired`, `rejected`); `embed_model_tag` text not null; `created_at` not null default now;
  `activated_at` null; `eval_run_id` bigint null, fk `eval_runs` on delete set null.
- Unique partial index on `status` where `status = 'active'`.

**`documents`**
- `id`; `corpus_version` bigint not null, fk `corpus_versions` on delete cascade; `doc_key` text not null;
  `title` text not null; `kind` text not null, check in (`resume`, `narrative`, `faq`, `repo`,
  `project_doc`, `site_doc`, `post`); `lang` text not null, check in (`en`, `pt`); `authority` smallint not
  null, check between 1 and 3; `source_url` text null; `updated` date null; `hash` text not null.
- Unique (`corpus_version`, `doc_key`).

**`chunks`**
- `id`; `document_id` bigint not null, fk `documents` on delete cascade; `corpus_version` bigint not null,
  fk `corpus_versions` on delete cascade; `heading_path` text not null; `content` text not null; `tokens`
  integer not null; `hash` text not null.
- Unique (`corpus_version`, `hash`); index on `corpus_version`.
- `embedding vector(N)` and `tsv tsvector`, with their HNSW and GIN indexes, are added by F01-T02 (see
  Decisions).

**`answer_cache`**
- `id`; `question_scrubbed` text not null; `lang` text not null, check in (`en`, `pt`); `corpus_version`
  bigint not null, fk `corpus_versions` on delete cascade; `model_config_version` text not null; `answer`
  jsonb not null; `trace_id` uuid null, fk `public_traces` on delete set null; `created_at` not null default
  now; `expires_at` not null; `hits` integer not null default 0.
- `embedding vector(N)` and its HNSW index are added by F01-T02.

**`public_traces`**
- `id` uuid pk (random); `created_at` not null default now; `expires_at` not null; `outcome` text not null,
  check in (`answered`, `abstained`, `declined`, `blocked`, `degraded`, `cached`); `intent` text null;
  `lang` text null; `corpus_version` bigint null (no fk: a trace outlives nothing it needs); 
  `model_config_version` text null; `latency_ms` integer null; `cost_usd` numeric(10,6) not null default
  0; `public_ok` boolean not null default false; `removed_at` null; `payload` jsonb not null default `{}`.
- Indexes on `created_at` and `expires_at`.

**`trace_spans`**
- `id`; `trace_id` uuid not null, fk `public_traces` on delete cascade; `step` text not null; `started_at`
  not null; `latency_ms` integer not null; `model`, `provider`, `fallback_reason` text null; `tokens_in`,
  `tokens_out` integer null; `cost_usd` numeric(10,6) not null default 0.
- Index on `trace_id`.

**`trace_removals`**
- `id`; `trace_id` uuid not null, fk `public_traces` on delete cascade; `removed_at` not null default now;
  `reason` text null.

**`questions_log`**
- `id`; `trace_id` uuid null, fk `public_traces` on delete set null; `created_at` not null default now;
  `question_scrubbed` text not null; `intent` text null; `outcome` text not null, same check as
  `public_traces.outcome`.

**`defense_events`**
- `id`; `created_at` not null default now; `trace_id` uuid null (no fk); `layer` text not null; `category`
  text not null; `action` text not null; `example_scrubbed` text null.
- Index on `created_at`.

**`log_events`**
- `id`; `at` not null default now; `level` text not null, check in (`debug`, `info`, `warning`, `error`);
  `source` text not null, check in (`app`, `langsmith`); `component` text not null; `event` text not null;
  `trace_id` uuid null, fk `public_traces` on delete set null; `fields` jsonb not null default `{}`.
- Index on `at`.

**`rate_buckets`**
- `key` text pk; `tokens` double precision not null; `refilled_at` not null.

**`budget_daily`**
- `day` date pk (UTC); `cost_usd` numeric(10,6) not null default 0; `requests` integer not null default 0;
  `paid_disabled_at` null; `degraded_at` null.

**`eval_runs`**
- `id`; `created_at` not null default now; `corpus_version` bigint not null (no fk: eval history outlives
  retired versions); `model_config_version` text not null; `run_kind` text not null, check in
  (`pre_release`, `nightly`); `dataset` text not null; `metrics` jsonb not null; `passed` boolean not null.

**`eval_results`**
- `id`; `eval_run_id` bigint not null, fk `eval_runs` on delete cascade; `question_id` text not null;
  `lang` text not null, check in (`en`, `pt`); `layer` text not null, check in (`retrieval`, `generation`,
  `end_to_end`); `career_fact` boolean not null default false; `passed` boolean not null; `reason`,
  `expected`, `answer` text null.
- Index on `eval_run_id`.

**`external_snapshots`**
- `id`; `taken_at` not null default now; `source` text not null, check in (`langsmith`, `uptimerobot`);
  `kind` text not null; `payload` jsonb not null.

**`alert_states`**
- `kind` text pk, check in (`budget_exhausted`, `providers_down`, `backup_failed`, `restore_failed`,
  `eval_regression`); `open_since`, `last_sent_at`, `closed_at` null. `site_down` has no row: UptimeRobot
  sends it by email itself (LLD "6.10 Alerts").

## Contract verification

- `GET /api/healthz` `200` ok and degraded: AC1.
- `GET /api/healthz` `503` down: AC1.
- Error body and `422`, `500`, `503` codes: AC5. (`403` and `429` are raised by F01-T04, which reuses the
  same catalog.)
- Outcome check on `public_traces` and `questions_log`: AC3.
- Unique partial index on active corpus version: AC3.
- The other checks, uniques and foreign keys are imposed by the database and checked against the
  migration at the end of the task; AC3 proves the migrations apply and match the reflection.

## Decisions

- Workspace members: `packages/core`, `apps/api`, `apps/dashboard`, `apps/ingest` (empty skeleton in this
  task) and `domains/zorzo`; Python 3.13 with one uv lockfile (ADR 0012).
- Dependency rules are enforced by import contracts (import-linter) run in CI and in a test, so a broken
  rule fails the build instead of waiting for review.
- The API uses SQLAlchemy Core's async engine on psycopg 3 and reflects the schema once at startup; no
  table or column is declared in API code.
- Every request gets a `trace_id` (random UUID) at entry, bound to the request's log context with
  structlog and returned in error bodies; the stored trace (F01-T10) reuses it.
- The health check reads `budget_daily` for today's UTC date to report `mode`; the database probe is one
  `SELECT 1` with a short timeout.
- `public_traces.public_ok` defaults to false, so a trace written without the route's judgment never shows
  in public.
- `questions_log.trace_id` and `log_events.trace_id` are set null on delete because both outlive or
  undercut the trace's retention; `eval_runs.corpus_version` and `public_traces.corpus_version` carry no
  foreign key so history survives the retention of versions.
- The vector and full-text columns (`chunks.embedding`, `chunks.tsv`, `answer_cache.embedding`) and their
  indexes are added by F01-T02, which settles the embedding model and so the vector size (LLD open
  question "Embedding model"); creating them here would force a guess and a migration to fix it.
- The test fixture creates `zorzo_test_<random>` per session through the compose Postgres (CI: a
  `pgvector/pgvector:pg17` service), runs the Django migrations against it and drops it at the end.
- The domain package holds one config file in this task (domain name and languages), validated by a
  Pydantic model at startup; later tasks add their own files to it.
- `GET /api/healthz` answers `503` with `mode: "unknown"` when the database is down: uptime monitors read
  the status code, and the mode can't be read without the database.
- The site-down alert goes by email straight from UptimeRobot, whose free plan has no Telegram and no
  webhooks; it never goes through the API, so it arrives when the VPS is down. A full outage (API
  container or VPS down) is seen by UptimeRobot as a `502`/`504` from Caddy or CloudFront, or a timeout.
- CloudFront must cache no error responses for `/api/*`, so a recovered API isn't reported as down; this is
  recorded in the LLD and applied by the infrastructure work.

## Files and interfaces

- `pyproject.toml` (workspace root), `uv.lock`: workspace members and shared dev tools (ruff, mypy,
  pytest, pytest-asyncio, import-linter).
- `packages/core`: `settings` base, `errors` (error catalog and `ApiError` types), `ports` package (empty
  protocols added by later tasks), `db` (async engine, reflection, `get_metadata()`).
- `apps/api`: FastAPI app factory `create_app(settings)`, startup that validates settings, loads the domain
  and reflects the schema; `GET /api/healthz`; exception handlers mapping the catalog; request middleware
  that sets `trace_id`.
- `apps/dashboard`: Django project and the `data` app with models and the first migration (creates the
  `vector` extension).
- `apps/ingest`: package skeleton with its settings class.
- `domains/zorzo`: package with `domain.yaml` and its Pydantic model.
- `infra/compose.yaml`: `postgres` (pgvector/pgvector:pg17), `api`, `dashboard` (runs migrations).
- `tests/`: conftest with the per-session database fixture; the test files named above.
- `.github/workflows/ci.yml`: ruff, mypy, import contracts, pytest with a Postgres service.
- `apps/api/AGENTS.md`, `apps/dashboard/AGENTS.md`: commands and practices of each app.

## Existing tests that change

None.

## Data changes

None.

## Documents updated

- `docs/lld.md`, section "Where each fact lives": `GLOSSARY.md` and `AGENTS.md` now exist.
- `docs/lld.md`, section "HTTP routes": `GET /api/healthz` returns `200` or `503` with `mode: unknown`, and
  CloudFront caches no error responses for `/api/*`.
- `docs/lld.md`, section "6.10 Alerts": `site_down` is sent by UptimeRobot by email, never through the API,
  with no row in `alert_states`.
- `docs/hld.md`, sections "System context", "Cross-cutting concepts" (Alerts), "Deployment and
  environments" and "Open questions": site-down alert by email from UptimeRobot; the open question about
  Telegram on UptimeRobot's free plan is closed.

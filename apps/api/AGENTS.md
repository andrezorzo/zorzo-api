# AGENTS.md — apps/api

FastAPI app: HTTP routes, the gate and the SSE stream. It wires the core and the domain at startup and holds
no business rules (LLD "Components and dependencies").

## Commands

Run from the repository root, with Postgres up (`docker compose -f infra/compose.yaml up -d postgres --wait`; first copy
`infra/.env.example` to `infra/.env`; an older `infra/.env` needs the `SALT_SEED` line added, since compose refuses to start without it).

| What | Command |
| --- | --- |
| Run the API locally | `DOMAIN=zorzo DATABASE_URL=postgresql://zorzo:zorzo@localhost:5432/zorzo APP_ENV=local TURNSTILE_SECRET=1x0000000000000000000000000000000AA SALT_SEED=local-salt-seed-change-me-0123456789 uv run python -m rag_api` (Turnstile's always-pass test secret; any token passes) |
| Run with the fake embedder and reranker (no Ollama, no model download) | add `EMBEDDER=fake RERANKER=fake` |
| Regenerate the stream contract after changing `rag_core.events` | `uv run python -m rag_core.events > contracts/sse-v1.schema.json` |
| Tests of this app | `uv run pytest tests/api tests/contract -q` |

`POST /api/v1/ask` needs an active corpus version embedded by the same model as the API (see
`apps/ingest/AGENTS.md`); otherwise it answers `503 unavailable`.

## Practices

- Routes raise `ApiError` from `rag_core.errors`; never build an error body by hand.
- Routes never touch the database or a provider directly: they call core services.
- Every request has a `trace_id`, bound to the log context and returned in error bodies; work that outlives
  the request (the stream) binds it again.
- Stream tests read the body with `tests/contract/sse.py`, which validates every event against
  `contracts/sse-v1.schema.json` and checks the ordering rules.

## Full verification

From the repository root: `uv run ruff check . && uv run ruff format --check . && uv run mypy . &&
uv run pytest -q && uv run pytest -m network -q` (the `network` tests call Cloudflare's real `siteverify` with its test secrets; mypy needs `DATABASE_URL` set to any value and `APP_ENV=local`; the import contracts run inside pytest).

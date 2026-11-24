# AGENTS.md — apps/api

FastAPI app: HTTP routes, the gate and the SSE stream. It wires the core and the domain at startup and holds
no business rules (LLD "Components and dependencies").

## Commands

Run from the repository root, with Postgres up (`docker compose -f infra/compose.yaml up -d postgres --wait`).

| What | Command |
| --- | --- |
| Run the API locally | `DOMAIN=zorzo DATABASE_URL=postgresql://zorzo:zorzo@localhost:5432/zorzo APP_ENV=local uv run python -m zorzo_api` |
| Tests of this app | `uv run pytest tests/api -q` |

## Practices

- Routes raise `ApiError` from `zorzo_core.errors`; never build an error body by hand.
- Routes never touch the database or a provider directly: they call core services.
- Every request has a `trace_id`, bound to the log context and returned in error bodies.

## Full verification

From the repository root: `uv run ruff check . && uv run ruff format --check . && uv run mypy . &&
uv run lint-imports --no-cache && uv run pytest -q` (mypy needs `DATABASE_URL` set to any value).

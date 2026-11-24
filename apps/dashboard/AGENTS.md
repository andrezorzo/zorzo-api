# AGENTS.md — apps/dashboard

Django: the public `/dash` pages (from F02) and the `data` app, which owns the schema of the whole system
(LLD "Data"). It never imports `core` (ADR 0015).

## Commands

Run from the repository root, with Postgres up.

| What | Command |
| --- | --- |
| Apply migrations | `DATABASE_URL=postgresql://zorzo:zorzo@localhost:5432/zorzo uv run python apps/dashboard/manage.py migrate` |
| Create a migration | `DATABASE_URL=… uv run python apps/dashboard/manage.py makemigrations data -n <what_changes>` |
| Schema tests | `uv run pytest tests/schema -q` |

## Practices

- Every default the API or ingest relies on is a database default (`db_default`), because they write SQL
  directly.
- Migrations stay backward compatible with the previous image: add first, remove in a later deploy.
- Hand-written SQL in a migration (`RunSQL`) carries a comment saying why.

## Full verification

Same as the repository: see `apps/api/AGENTS.md`, section "Full verification".

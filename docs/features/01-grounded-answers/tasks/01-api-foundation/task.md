# 01: API foundation

**Status:** done

**What to build:** A developer can bring up the local environment and see the API report its health: the uv workspace with the shared core, the apps and the domain package; settings that stop the app at startup when something required is missing; Postgres with pgvector in the compose file; the Django schema owner with the tables F01 writes; the API reflecting them; and the test base (a test database per session with the migrations applied) running in CI with lint and type checks.

**Decisions owned:** the HTTP error body and catalog; the outcome values.

**Blocked by:** None (can start immediately).

- [x] **AC1** · `GET /api/healthz` answers `{status: ok, mode: normal, db: ok}` with the database up and reports `db: down` when it isn't, per LLD "HTTP routes".
- [x] **AC2** · The workspace follows LLD "Code structure" and "Components and dependencies" (ADR 0012); `DOMAIN` selects the domain package and `core` never imports apps or domains.
- [x] **AC3** · Django migrations create every table F01 writes (LLD "Data"), including the outcome values `answered`, `abstained`, `declined`, `blocked`, `degraded` and `cached`; the API reflects the schema with SQLAlchemy Core and keeps no copy of the columns.
- [x] **AC4** · A missing required setting or an invalid config file stops the app before it serves (LLD "Configuration").
- [x] **AC5** · The HTTP error body and catalog of LLD "Errors" exist in one place that later routes reuse.
- [x] **AC6** · CI runs ruff, mypy and pytest; the test fixture creates a test database per session and applies the migrations.

# 15: Retention job

**Status:** to do

**What to build:** Visitors see no change. Every stored record that has an expiry is deleted when it expires, so questions, traces, logs, cached answers, rate buckets, old corpus versions and idle conversations don't pile up: the nightly retention job of LLD "6.11 Retention", run by a systemd timer as LLD "7. Background processes" says. Added after the breakdown, from the F01-T07 plan review: the conversation threads in the `langgraph` schema had no task that deleted them.

**Decisions owned:** how the Django command reads the retention values without importing the core (ADR 0015); how an idle LangGraph thread is found from its latest checkpoint; the systemd service and timer units.

**Decisions consumed from other tasks:** The `langgraph` schema and the stored conversation (07); the stored trace tables and the `error` outcome (10); the answer cache rows (09).

**Blocked by:** 07 Conversation and language edges.

- [ ] **AC1** · A Django command deletes `questions_log` rows older than `RETENTION_QUESTIONS`, `public_traces` with their `trace_spans` older than `RETENTION_PUBLIC_TRACES`, `log_events` older than `RETENTION_LOGS` and `answer_cache` rows older than `CACHE_TTL` (LLD "6.11 Retention"); rows inside their windows stay, and a questions-log row whose trace is deleted keeps its row with `trace_id` null.
- [ ] **AC2** · `retired` and `rejected` corpus versions past `RETENTION_RETIRED_VERSIONS` are deleted with their documents and chunks; the `active` version and candidates are never deleted.
- [ ] **AC3** · `rate_buckets` rows not refilled for more than a day are deleted.
- [ ] **AC4** · LangGraph threads whose latest checkpoint is older than `SESSION_TTL` are deleted with all their rows in the `langgraph` schema; a thread with a newer checkpoint keeps every row.
- [ ] **AC5** · A second run right after the first deletes nothing more; a failed run writes a `log_events` row and exits with an error, and the next run deletes what it missed (LLD "7. Background processes").
- [ ] **AC6** · A systemd service and a nightly timer under `infra/` run the command.

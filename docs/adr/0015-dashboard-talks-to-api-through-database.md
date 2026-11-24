---
status: accepted
---

# 0015 · The dashboard talks to the API only through the database

The Django dashboard renders public pages from data the API writes: traces, spans, defense events, eval
runs, budget. It reads Postgres directly and never imports `core` nor calls the API; its only outside calls
are its own snapshot pulls (LangSmith, UptimeRobot). The database schema, owned by Django migrations, is the
contract between the two apps.

Discarded: Django importing `core` (shared code, but the dashboard would pull LangChain, LangGraph and the
adapters into its image and couple both release cycles); Django calling JSON endpoints on the API (a clean
service boundary, but a second copy of every read model, more routes to secure, and dashboard pages that
fail when the API is down).

Cost accepted: the schema is a shared contract, so changes must stay backward compatible across both apps
(HLD flow 6.4); aggregation queries live in Django, separate from the code that writes the rows.

Interview answer: "Two frameworks, one contract: the schema. The API writes, Django reads and owns the
migrations. No shared imports, no internal API, and the dashboard stays up even when the API is down."

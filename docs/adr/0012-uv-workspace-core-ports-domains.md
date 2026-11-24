---
status: accepted
---

# 0012 · uv workspace: a shared core with ports and adapters, plus domain folders

The API, the ingest job and the evals use the same retrieval, model gateway, chunking and agent graph, and
the system must be adaptable to other domains (ADR 0011). The code is one uv workspace: `packages/core`
holds the generic logic, with ports (interfaces) for everything outside and adapters as the only place with
network clients; `apps/api`, `apps/ingest` and `apps/dashboard` have their own images; `domains/<name>` holds
the corpus, prompts, intents, tools and eval datasets, chosen by `DOMAIN`.

Discarded: one `pyproject` per service with ingest inside the API (a common layout, but the core code ends up
copied or imported across services, and core and domain mix); a single flat package (simplest, but nothing
stops domain details from leaking into the core, and every image ships everything).

Cost accepted: more structure up front (ports, wiring at startup, a workspace) and some indirection when
reading the code; `docs/code-map.md` exists to make it navigable.

Interview answer: "One workspace, one shared core behind ports and adapters, three thin apps, and a domain
folder. Ingest, API and evals run the exact same retrieval code, and a new domain is a new folder, not a
fork of the core."

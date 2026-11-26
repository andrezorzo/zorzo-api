---
status: accepted
---

# 0011 · A generic core with domain parts as content and configuration

The code is public and meant to be a reference implementation that other teams can adapt (a law firm
searching case files, an engineering firm searching projects). The core (gate, guard, agent graph,
retrieval, verification, model gateway, telemetry, ingest, eval harness) knows nothing about André. The
domain parts are content and configuration: corpus, persona and prompts, intents, tools, eval datasets,
and the public layer, which can be switched off for a private deployment.

Discarded: building for this site only (faster at first, but adapting it means a rewrite, and it loses the
reference and freelance value); a multi-tenant framework with plugins and a configuration UI (over-built
for a 15-day MVP and a single owner).

Cost accepted: discipline to keep domain specifics out of the core, and some indirection (intents and
tools come from configuration); adapting still means a fork plus a new eval set for the new domain.

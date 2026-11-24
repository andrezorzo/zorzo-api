# Roadmap — zorzo.dev.br

2026-11-24 · André de Castro Zorzo

The features to build, the order between them and the status of each. The goal of each phase is in the
[product brief](product-brief.md), section "Roadmap". What a feature is, its folder and when each status
changes are in [`AGENTS.md`](../AGENTS.md), section "Planning".

**Status:** planned → PRD ready → spec ready → in progress → done.

The ID links to the feature's folder in `docs/features/` once it exists.

## Phase 1: MVP launch (2026-12-04)

If the date is at risk, cut in this order: the blocked attempts page (F04) → the evals page (F03; evals
keep running) → the agent tools (F04; direct answers with sources stay).

| ID | Feature | At the end the visitor can | Depends on | Status |
| --- | --- | --- | --- | --- |
| [F01](features/01-grounded-answers/) | Grounded answers | Ask about André's career, projects or how the site was built, in English or Portuguese, watch the steps live and get a cited answer, an honest "not in my sources" or a polite decline; follow up in the same conversation; keep using search when the AI is down; reach André. | — | in progress |
| [F02](features/02-transparency/) | Transparency | Open and share the trace of any answer, see the system's activity, status and budget, and the list of sources. | F01 | PRD ready |
| [F03](features/03-evals/) | Evals | See the eval results of the live system per layer and language, the trend across versions and what failed. | F01, F02 | PRD ready |
| [F04](features/04-agent-and-defenses/) | Agent and defenses | Ask what André is working on now and about specific projects and documents, see why a request was blocked, and see the attacks each defense stopped. | F01 | PRD ready |

## Phase 2: after launch

TBD. Candidates from the product brief: event log with solved cases, career graph, public comparison of
models, providers and rerankers. The 90-day metrics decide the order.

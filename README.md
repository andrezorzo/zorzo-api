# zorzo-api

Backend of [zorzo.dev.br](https://zorzo.dev.br), the professional site of André de Castro Zorzo. Visitors ask
questions about his work and an AI agent answers from a curated, reviewed corpus, citing its sources. The
known AI risks (hallucination, prompt injection, abuse, cost) are contained in public, with numbers.

This repository holds the API (FastAPI + LangGraph), the public dashboards (Django), corpus ingestion, evals,
infrastructure and the system-wide design docs. The static front lives in
[zorzo-site](https://github.com/andrezorzo/zorzo-site).

Status: design phase. No code yet.

## Docs

- [Product brief](docs/product-brief.md): what the site is and for whom.
- [HLD](docs/hld.md): how the system is organized and why.
- [LLD](docs/lld.md): how each part works inside.
- [ADRs](docs/adr/): the decisions, with the alternatives discarded.
- [Roadmap](docs/roadmap.md): the features, their order and status; each has a PRD in `docs/features/`.
- [Glossary](GLOSSARY.md): the project's vocabulary.
- [AGENTS.md](AGENTS.md): conventions for agents and contributors.

## License

[MIT](LICENSE)

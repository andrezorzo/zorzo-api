# 12: Site baseline (zorzo-site)

**Status:** in progress

**What to build:** The current site (the 3D brain hero and the career, work, projects and contact content below it) lives in `zorzo-site` and looks and behaves as it does today, still answering from its local mock. The question box works with or without 3D graphics, the brain reads the data format the ingest publishes, and the front has its own LLD. Added after the breakdown as the prefactor of 03: it makes 03 a swap of the mock for the API.

**Decisions owned:** the front stack and module layout; how published corpus chunks become points in the brain.

**Decisions consumed from other tasks:** The published data format and the example questions format (02).

**Blocked by:** 02 Ingest and search-only answers.

- [ ] **AC1** · The page from the local `site/` folder (hero and content below) is served from `zorzo-site` and matches today's page, checked side by side on desktop and mobile widths.
- [ ] **AC2** · The script is split by responsibility: the brain scene, the interface (question box, panels, cards, language) and the data access, with the local mock behind one function that 03 replaces with the API client.
- [ ] **AC3** · Without WebGL or with reduced motion, the question box and the answer panel work against the mock and the brain is a still image (today the box is hidden).
- [ ] **AC4** · The brain reads the data the ingest publishes (`manifest.json` → `corpus.json` with each chunk's 3D position, and `examples.json`); development uses a demo copy of today's corpus in the published format; the tests use the published data of the fixture corpus.
- [ ] **AC5** · `zorzo-site` has its LLD (stack, modules, the brain's data contract, the fallback without 3D, the brain mesh attribution) and its AGENTS.md commands (serve locally, test), with Playwright running one smoke test in CI.

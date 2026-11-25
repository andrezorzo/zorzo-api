# 03: Web foundation (zorzo-site)

**Status:** in progress

**What to build:** A visitor opens the page, sees example questions in their interface language, asks a question and sees the matching passages in search-only mode; the page tells online, degraded and offline apart and works without 3D graphics. Lives in `zorzo-site`.

**Decisions consumed from other tasks:** The SSE schema, the request shape and the example questions format (02); the front stack, module layout and the brain's data contract (12).

**Blocked by:** 02 Ingest and search-only answers; 12 Site baseline.

- [ ] **AC1** · The question box sends `POST /api/v1/ask` with `fetch` and reads the stream; a new question cancels the current one (LLD "Front against the API").
- [ ] **AC2** · 4–6 example questions show in the interface language and switch with it; clicking one asks it.
- [ ] **AC3** · The conversation id is a UUID kept in the tab's session storage and survives a reload.
- [ ] **AC4** · `GET /api/healthz` drives the online, degraded and offline states; offline disables the box and recovers by itself.
- [ ] **AC5** · In degraded mode the page shows the notice and up to `DEGRADED_MAX_PASSAGES` passages with title and link, or "no matching sources for this question" with the examples.
- [ ] **AC6** · Without WebGL or with reduced motion, asking and reading work the same and the brain is a still image.
- [ ] **AC7** · Playwright tests run against recorded streams that validate against the SSE schema.

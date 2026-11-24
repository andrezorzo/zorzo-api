# 04: Gate

**Status:** to do

**What to build:** A visitor who passes the bot check and stays within the limits gets an answer; one who doesn't gets a clear HTTP refusal before any model call, and the page explains it.

**Decisions consumed from other tasks:** The HTTP error body and catalog (01); the request shape (02).

**Blocked by:** 02 Ingest and search-only answers; 03 Web foundation.

- [ ] **AC1** · Every question carries a fresh Turnstile token verified server-side before anything else; a provider error refuses the request (LLD "Security").
- [ ] **AC2** · Input is normalized and capped at `MAX_QUESTION_CHARS`; over the cap is `422 invalid_input`.
- [ ] **AC3** · Rate limits per hashed IP and per session follow LLD "6.7 Rate limit"; over the limit is `429` with `Retry-After`; concurrent requests can't both pass.
- [ ] **AC4** · The raw IP is never stored or logged; only the daily HMAC is.
- [ ] **AC5** · The page reacts to 422, 403, 429 and 503 as in LLD "Front against the API".
- [ ] **AC6** · Tests use Turnstile's test keys (always pass, always fail).
- [ ] **AC7** · An unknown path answers `404 not_found` and a wrong method `405 method_not_allowed`, with the catalog body and a `trace_id` (LLD "Errors").

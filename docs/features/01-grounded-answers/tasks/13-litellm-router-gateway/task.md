# 13: Model gateway on the LiteLLM Router

**Status:** in progress

**What to build:** The model gateway keeps answering the same way for visitors, but the free part of each stage's chain runs on the LiteLLM Router (order, cooldowns, timeouts) instead of our own breaker and quota code. Only the paid entry, which spends money, stays in our code with its reserve-then-settle budget. The owner's rule: use a maintained library where it covers the need, and write only what it lacks.

**Decisions owned:** the models config shape for the Router; which gateway rules move to LiteLLM and which stay ours.

**Decisions consumed from other tasks:** The ModelGateway port, the budget, `mode()` and the alerts (05).

**Blocked by:** 05 Model gateway and budget.

- [ ] **AC1** · The free entries of each stage run on a LiteLLM Router (SDK, in the API process, no proxy, no Redis) in a fixed priority order, with no same-entry retry; a 429, a 5xx, a timeout or a high failure rate puts an entry in cooldown, for `Retry-After` when the provider sends it (LLD "6.6 Circuit breaker").
- [ ] **AC2** · The paid entry runs outside the Router, only when the free chain is exhausted: reserve in Postgres, call, settle from the provider-reported cost or the estimate (LLD "6.2 Daily budget").
- [ ] **AC3** · Streaming falls back only before the first token; the request deadline and `REQUEST_COST_CAP_USD` still bound every call.
- [ ] **AC4** · The remote rerank runs through the Router and falls to the local level on failure (ADR 0009).
- [ ] **AC5** · `mode()` and the alerts read the Router's cooldowns; cooldown starts and ends are written to `log_events`.
- [ ] **AC6** · Our breaker, the timeout-blame rule, the header-based quota and the OpenAI-compatible chat adapter are removed; the gateway tests at the port still pass, minus the removed rules.
- [ ] **AC7** · LiteLLM is pinned to an exact version and makes no network call at import or Router build (no remote cost map).

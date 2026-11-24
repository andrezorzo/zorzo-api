# 05: Model gateway and budget

**Status:** to do

**What to build:** The system calls models through per-stage chains across providers and never spends past the budget cap: when the budget runs out or every provider is down, questions are answered in degraded mode and André is alerted.

**Decisions owned:** the model config format and stage names.

**Decisions consumed from other tasks:** The SSE schema (02).

**Blocked by:** 02 Ingest and search-only answers.

- [ ] **AC1** · The ModelGateway tries a stage's chain in order, respects the deadline and the remaining budget, skips open breakers and raises "unavailable" when the chain is exhausted (LLD "Components and dependencies", ADR 0007).
- [ ] **AC2** · Breakers follow LLD "6.6 Circuit breaker"; budget states follow LLD "6.2 Daily budget" with reserve-then-settle; free quota follows LLD "6.8 Free quota".
- [ ] **AC3** · With the budget at the cap, or all providers failing, a question gets the `degraded` event with the reason and no model call.
- [ ] **AC4** · Alerts `budget_exhausted` and `providers_down` open once per incident through the Alerter port (LLD "6.10 Alerts").
- [ ] **AC5** · The remote rerank level is added in front of the local one and falls through on failure (ADR 0009).
- [ ] **AC6** · The fake OpenRouter server answers scripted responses, 429s, hangs and malformed JSON for these tests.
- [ ] **AC7** · Model config (stages, chains, prices) loads from the config files and is part of `model_config_version`.

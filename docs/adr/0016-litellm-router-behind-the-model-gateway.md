---
status: accepted
---

# 0016 · The LiteLLM Router runs the free chain behind the model gateway

The free entries of each stage's chain (ADR 0007) run on a LiteLLM Router, from the SDK, inside the API
process: one model group per stage, entries tried in a fixed priority order, no same-entry retries, and
LiteLLM's cooldowns in place of our own circuit breaker. The paid entry stays outside the Router, in our
gateway, because it is the only one that spends money: it reserves its estimate in Postgres before the call
and settles after it. The gateway keeps the port (`complete`, `stream`, `rerank`, `mode`), the request
deadline, the per-request cost cap, the degraded mode and the alerts. LiteLLM is pinned to an exact,
reviewed version and makes no network call at import.

The owner's rule: use a maintained library where it covers the need, and write only what it lacks. A gateway
of our own (breaker, quota, provider adapter) was built and reviewed first (F01-T05); the Router replaces the
parts it covers.

Discarded: our own gateway (it works and its rules are tested, but every breakage is ours to fix, while
LiteLLM is widely used and maintained); the LiteLLM Proxy server (virtual keys, spend per key and an admin
UI are for many apps or teams sharing providers; here it would be another container with its own database
on an 8 GB VPS, one more network hop and an admin key to protect); the Router's own budget for the paid
entry (it checks only spend already recorded, so concurrent calls can pass the cap together, over a rolling
window rather than the UTC day, in memory only).

Cost accepted: the cooldown has no half-open trial call and no rule about a timeout cut short by the
request's deadline; free-tier limits are learned from the provider's 429 instead of read ahead from headers;
a dependency with a large surface and a supply-chain history (versions 1.82.7 and 1.82.8 were malicious,
March 2026), handled by an exact pin, releases at least two weeks old with a matching GitHub release, and
upgrades as reviewed PRs.

Interview answer: "I first built the fallback chain myself to understand the failure modes, then moved the
free part onto LiteLLM's Router, because a maintained library beats code I have to keep alive. I kept the
paid call outside it: its budget check isn't atomic under concurrency, and with a hard ten-cent daily cap I
reserve the cost in Postgres before calling."

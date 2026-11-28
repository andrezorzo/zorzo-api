# 09: Answer cache

**Status:** done

**What to build:** A visitor asking almost the same question as an earlier one gets the verified answer reused at no cost, labeled as reused; in degraded mode the first question of a conversation can still get one.

**Decisions consumed from other tasks:** The `route` output fields (06); the SSE schema (02).

**Blocked by:** 06 Verified answers with citations.

- [x] **AC1** · Lookup, hit, storage and expiry follow LLD "6.9 Answer cache" (ADR 0014); only answered and verified responses are stored.
- [x] **AC2** · A hit streams `stage`, the cached `claim` events, `usage` with no answer or judge cost (0 unless `route` ran on the paid entry) and `done {cached: true}`, with no `token`, and shows the step "reused a verified answer to the same question".
- [x] **AC3** · In degraded mode, the first question of a conversation is looked up with the raw question and a local language detector, without `route`; follow-ups go to search-only. The detector library is recorded in the LLD.
- [x] **AC4** · A hit still counts against the rate limit.

# 08: Guard, decline and block

**Status:** done

**What to build:** Off-topic and out-of-bounds requests are declined politely with a suggested question, attacks are blocked with the layer and reason shown, and contact links follow every reply.

**Decisions consumed from other tasks:** The `route` output fields and the stream semantics of abstention and decline (06); the example questions (02).

**Blocked by:** 06 Verified answers with citations.

- [x] **AC1** · Guard layers per HLD "Cross-cutting concepts" and LLD "6.4 Answer contract and verification": input checks, untrusted-content marking, output checks with the canary and the rules about a real person; the L2 classifier is chosen and recorded in the LLD.
- [x] **AC2** · Off-topic, personal-life and former-employer questions are declined; attacks produce `blocked` followed by `done`, and a `defense_events` row.
- [x] **AC3** · On an abstention or decline, `done.suggestion` is the id of the closest example question by local embedding similarity, with no model call.
- [x] **AC4** · The `contact` intent returns the contact options from the domain config (LinkedIn, GitHub, email) without a model answer.
- [x] **AC5** · The page shows the block message with layer and reason, the suggested question, and the contact links after every answer, abstention, decline and degraded reply.
- [x] **AC6** · A gate refusal for the bot check or the rate limit writes a `defense_events` row (layer `L0` `bot` or `L1` `rate_limit`), with the request's trace id and no question text.

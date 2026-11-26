---
status: accepted
---

# 0014 · Semantic answer cache in Postgres (pgvector)

Recruiters repeat questions ("years of experience?"), and each answer costs 4 to 5 calls of a small free
quota. Near-duplicate questions get the stored answer: similarity ≥ `CACHE_SIMILARITY` on the embedding of the
scrubbed standalone question, same language, same corpus and model configuration versions, not expired. The
lookup is a nearest-neighbor query, so it lives in Postgres with pgvector, filtered in the same `WHERE`; only
verified answers are stored, and the trace marks a cached answer.

Discarded: Redis (plain Redis is key-value and can't do similarity without a vector search module, which
adds a service and RAM; and the store's milliseconds don't matter next to the seconds of LLM calls the cache
saves); no cache (every answer is a live demo and there is less code, but repeated questions spend quota).

Cost accepted: one more table and a retention rule; a cached answer doesn't show the pipeline running live;
a near-duplicate threshold that is too loose could serve an answer to a slightly different question
(calibrated on the eval set).

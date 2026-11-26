---
status: accepted
---

# 0009 · Rerank chain: provider, then local, then none

After hybrid search and fusion, candidates are reordered by a chain: NVIDIA Nemotron rerank 8B on
OpenRouter's free tier with a short timeout, then a local multilingual cross-encoder (ONNX, inside the
API), then no rerank (fusion, authority and diversity only). The chain keeps answers flowing when the
external reranker is slow or out of quota. Which level leads is decided by the M5 benchmark: the cheapest
variant within the noise band of the best one.

Discarded: a paid rerank API such as Cohere, Jina or Voyage (strong, but beyond the budget); no rerank at
all (simplest, but hybrid results are noisy, and the loss is measured at M5); local only (no external
dependency, but likely weaker, also measured at M5).

Cost accepted: another external dependency with its own quota; a fallback adds latency to the request;
Portuguese quality of each reranker is unknown until measured.

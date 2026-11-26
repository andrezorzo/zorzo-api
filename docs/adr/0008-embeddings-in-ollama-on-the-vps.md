---
status: accepted
---

# 0008 · Embeddings in Ollama on the VPS, with ingest on the same machine

Query and document embeddings come from one Ollama service on the VPS, with the model tag pinned per
corpus version and the model kept loaded. Ingest runs on the same machine (started by a GitHub Action
over SSH), so documents and queries are always embedded by the identical model and runtime. There is no
cost per call and no external dependency on the query path, and the same Ollama serves answers in the
local profile.

Discarded: an embedding API such as OpenAI or Voyage (cheap per call, but another key and provider on
every query, added network latency, and model versions that can change underneath); an in-process
library in the API (the model loaded in every worker, more RAM, and API restarts reload it); embedding in
GitHub Actions (a different runtime from query time, so vectors could drift).

Cost accepted: CPU-only latency on a shared vCPU; Ollama's RAM on an 8 GB machine; the model choice
(multilingual candidates) is still open and decided by recall in the LLD.

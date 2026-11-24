# Ollama check (AC2)

Manual check of the Ollama embedding adapter, which the test suite replaces with the deterministic fake.
Run on 2026-11-24 on the dev machine (CPU only).

## Setup

- `ollama/ollama:0.40.17` from `infra/compose.yaml`, model `bge-m3:567m` pulled.
- A fresh migrated database; the fixture corpus (`tests/fixtures/corpus`, 3 documents).
- Ingest: `EMBEDDER=ollama APP_ENV=local uv run python -m rag_ingest --source tests/fixtures/corpus --activate`.
- API in process with `EMBEDDER=ollama` and `RERANKER=local` (the real ONNX cross-encoder).

## Result

Ingest exited 0 in 6.0 s:
`{"version": 1, "status": "active", "documents": 3, "chunks": 7, "embedded": 7, "copied": 0}`.

| Question | Status | Time | Top hits (title, rerank score) |
| --- | --- | --- | --- |
| Which AWS services has he used? | 200 | 254 ms | FAQ 3.86, FAQ −1.33, Narrativa de carreira −3.14 |
| Como o site confere as respostas? | 200 | 166 ms | How this site works −1.92, How this site works −3.58, Narrativa de carreira −6.61 |
| zyxwvut qqqq | 200 | 91 ms | none (below `search_min_similarity`, no full-text match) |

- Vectors have 1024 dimensions and the version carries the tag `bge-m3:567m`.
- A Portuguese question found the English source about verification (multilingual embeddings).
- The first attempt ingested with the fake embedder by mistake; the API with Ollama answered `503
  unavailable` to every question, which is the embedding-tag rule of LLD "6.1 Corpus version" working.

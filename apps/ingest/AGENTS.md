# AGENTS.md — apps/ingest

One-shot job: runs the core ingest pipeline for a domain (LLD "6.1 Corpus version"). It holds no rules of its
own: the gate, chunking, writes, activation and publishing live in `rag_core.ingest`.

## Prerequisites

- **gitleaks 8.30.1** on the `PATH` (the ingest gate and its tests need it). The CI and the `ingest` image
  install the same version, checked against the release checksum. On Ubuntu, not from apt (too old):

  ```bash
  cd /tmp && V=8.30.1 \
  && curl -sSLO https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_linux_x64.tar.gz \
  && curl -sSLO https://github.com/gitleaks/gitleaks/releases/download/v$V/gitleaks_${V}_checksums.txt \
  && grep linux_x64.tar.gz gitleaks_${V}_checksums.txt | sha256sum -c - \
  && tar -xzf gitleaks_${V}_linux_x64.tar.gz gitleaks && mkdir -p ~/.local/bin && mv gitleaks ~/.local/bin/
  ```

- **Ollama** with the pinned model for real embeddings (`EMBEDDER=ollama`, the default):
  `docker compose -f infra/compose.yaml up -d ollama` then
  `docker compose -f infra/compose.yaml exec ollama ollama pull bge-m3:567m`. Without it, `EMBEDDER=fake`
  (local only) embeds deterministically.

## Commands

Run from the repository root, with Postgres up and migrated.

| What | Command |
| --- | --- |
| Ingest the domain corpus into a candidate | `APP_ENV=local DOMAIN=zorzo DATABASE_URL=postgresql://zorzo:zorzo@localhost:5432/zorzo uv run python -m rag_ingest` |
| …and activate it (local only without an eval run) | add `--activate` |
| Ingest another folder | add `--source <folder>` |
| Activate an existing candidate | `… uv run python -m rag_ingest --activate-version <N>` |
| Tests of this app | `uv run pytest tests/ingest -q` |

The command prints one line `{"summary": {...}}` (version, status, documents, chunks, embedded, copied) and
exits 1 on a rejected run, a refused activation or when another run holds the lock.

## Practices

- Corpus files are Markdown with YAML front matter (`doc_key`, `title`, `kind`, `lang`, `authority`,
  `source_url`, `updated`, `reviewed_by_owner`); they live in the domain package's `corpus/` folder.
- Never log a gate match: a problem names the file and the rule only.
- Test documents with a secret or personal data are generated at test time, never committed.

## Full verification

Same as the repository: see `apps/api/AGENTS.md`, section "Full verification".

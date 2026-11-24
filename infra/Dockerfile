# One image definition, one target per app (ADR 0012).
FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
COPY domains domains
COPY config config
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
# Run as an unprivileged user, so a bug in the app cannot act as root inside the container.
RUN useradd --system --uid 10001 --no-create-home app
ENV HOME=/tmp

FROM base AS dashboard
USER app
CMD ["python", "apps/dashboard/manage.py", "migrate", "--noinput"]

FROM base AS api
# The local rerank model is baked in and loaded offline (LLD "Libraries": local rerank).
ENV RERANK_CACHE_DIR=/app/models/rerank ORT_DISABLE_TELEMETRY=1
RUN python -c "from pathlib import Path; from rag_core.adapters.local_reranker import LocalReranker; \
LocalReranker(Path('/app/models/rerank')).model()" \
 && chown -R app /app/models
ENV HF_HUB_OFFLINE=1
USER app
EXPOSE 8000
CMD ["python", "-m", "rag_api"]

FROM base AS ingest
# gitleaks, pinned and checked against the release checksum (apps/ingest/AGENTS.md).
ARG GITLEAKS_VERSION=8.30.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && cd /tmp \
 && curl -sSLO https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz \
 && curl -sSLO https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt \
 && grep linux_x64.tar.gz gitleaks_${GITLEAKS_VERSION}_checksums.txt | sha256sum -c - \
 && tar -xzf gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz -C /usr/local/bin gitleaks \
 && rm -f gitleaks_* && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && mkdir /data && chown app /data
USER app
CMD ["python", "-m", "rag_ingest"]

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
# The local rerank and injection classifier models are baked in and loaded offline (LLD "Libraries").
ENV RERANK_CACHE_DIR=/app/models/rerank GUARD_CACHE_DIR=/app/models/guard ORT_DISABLE_TELEMETRY=1
# LiteLLM fetches its model cost map from GitHub at import unless told to use the bundled one (ADR 0016).
ENV LITELLM_LOCAL_MODEL_COST_MAP=True
RUN python -c "from pathlib import Path; from rag_core.adapters.local_reranker import LocalReranker; \
LocalReranker(Path('/app/models/rerank')).model()" \
 && python -c "from pathlib import Path; from rag_core.adapters.prompt_guard import PromptGuardClassifier; \
PromptGuardClassifier(Path('/app/models/guard')).model()" \
 && chown -R app /app/models
ENV HF_HUB_OFFLINE=1
USER app
EXPOSE 8000
CMD ["python", "-m", "rag_api"]

FROM base AS ingest
# gitleaks, pinned by version and by the tarball hash below (the owner of both values).
ARG GITLEAKS_VERSION=8.30.1
# The tarball's SHA-256, fixed here: a checksums file from the same release can't vouch for a tampered one.
ARG GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && cd /tmp \
 && curl -sSLO https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz \
 && echo "${GITLEAKS_SHA256}  gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" | sha256sum -c - \
 && tar -xzf gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz -C /usr/local/bin gitleaks \
 && rm -f gitleaks_* && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && mkdir /data && chown app /data
USER app
CMD ["python", "-m", "rag_ingest"]

# One image definition, one target per app (ADR 0012).
FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps apps
COPY domains domains
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
# Run as an unprivileged user, so a bug in the app cannot act as root inside the container.
RUN useradd --system --uid 10001 --no-create-home app
ENV HOME=/tmp
USER app

FROM base AS dashboard
CMD ["python", "apps/dashboard/manage.py", "migrate", "--noinput"]

FROM base AS api
EXPOSE 8000
CMD ["python", "-m", "rag_api"]

"""FastAPI app factory: validates settings, loads the domain and reflects the schema at startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import structlog
from fastapi import FastAPI
from rag_core.adapters.factory import build_embedder, build_rerankers
from rag_core.adapters.pgvector_store import PgVectorStore
from rag_core.adapters.postgres import Database
from rag_core.domain import load_domain
from rag_core.limits import load_limits
from rag_core.telemetry import configure_logging

from rag_api import ask, errors, health
from rag_api.settings import ApiSettings

log = structlog.get_logger()


async def _warm_rerankers(app: FastAPI) -> None:
    """Load rerank models at startup, so the first visitor after a deploy doesn't wait for it."""
    for level in app.state.rerankers:
        try:
            await anyio.to_thread.run_sync(level.warm)
        except Exception:
            # Not fatal: the rerank chain falls to the next level at question time (ADR 0009).
            log.warning("rerank_warmup_failed", level=type(level).__name__, exc_info=True)


def create_app(settings: ApiSettings) -> FastAPI:
    configure_logging(settings.app_env, settings.log_level)
    # The domain package (domain_<name>, ADR 0011) holds what makes the generic core about one
    # person: languages, contact, prompts. DOMAIN selects it.
    domain = load_domain(settings.domain)
    limits = load_limits(settings.limits_file)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await db.reflect()
            await _warm_rerankers(app)
            yield
        finally:
            await db.close()

    app = FastAPI(title="zorzo-api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.domain = domain
    app.state.db = db
    app.state.limits = limits
    app.state.embedder = build_embedder(settings)
    app.state.rerankers = build_rerankers(settings, settings.reranker, settings.rerank_cache_dir)
    app.state.vector_store = PgVectorStore(db, limits.rrf_k, limits.search_min_similarity)
    app.state.trace_sinks = []  # extra sinks next to the stream (stored trace, F01-T10)
    errors.install(app)
    app.include_router(health.router)
    app.include_router(ask.router)
    return app

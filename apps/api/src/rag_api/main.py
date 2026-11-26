"""FastAPI app factory: validates settings, loads the domain and reflects the schema at startup."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import structlog
from fastapi import FastAPI
from rag_core.adapters.alerts import LogAlerter, TelegramAlerter
from rag_core.adapters.factory import (
    build_embedder,
    build_gateway,
    build_providers,
    build_rerankers,
    rerank_chain,
)
from rag_core.adapters.pgvector_store import PgVectorStore
from rag_core.adapters.postgres import Database
from rag_core.adapters.rate_limit import PostgresRateLimiter
from rag_core.adapters.turnstile import TurnstileBotCheck
from rag_core.domain import load_domain
from rag_core.limits import Limits, load_limits
from rag_core.models_config import config_version, load_models
from rag_core.ports import Alerter
from rag_core.services.gate import Gate
from rag_core.services.gateway import ModelGateway
from rag_core.telemetry import configure_logging

from rag_api import ask, errors, health
from rag_api.body_limit import BodyLimit
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


def _alerter(settings: ApiSettings) -> Alerter:
    # Settings require both Telegram values in production; locally the alert is a log line.
    if settings.app_env == "production" and settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramAlerter(
            settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id.get_secret_value()
        )
    return LogAlerter()


def _gateway(settings: ApiSettings, limits: Limits, db: Database) -> tuple[ModelGateway, str]:
    """The model gateway and the `model_config_version` of what it loaded (LLD "Configuration")."""
    models = load_models(settings.models_path)
    providers = build_providers(models, settings.provider_keys(), settings.app_env)
    return build_gateway(models, providers, db, _alerter(settings), limits), config_version(models, limits)


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
            # Each close runs even when an earlier one raises; the database closes last, after the
            # gateway's pending budget settles and alerts.
            try:
                await app.state.gate.bot_check.close()
            finally:
                try:
                    await app.state.gateway.close()
                finally:
                    await db.close()

    app = FastAPI(title="rag-api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.domain = domain
    app.state.db = db
    app.state.limits = limits
    app.state.embedder = build_embedder(settings)
    app.state.gateway, app.state.model_config_version = _gateway(settings, limits, db)
    # Degraded questions use the local levels only, so they make no model call.
    app.state.local_rerankers = build_rerankers(settings)
    app.state.rerankers = rerank_chain(app.state.gateway, app.state.local_rerankers)
    app.state.vector_store = PgVectorStore(db, limits.rrf_k, limits.search_min_similarity)
    app.state.gate = Gate(
        TurnstileBotCheck(settings.turnstile_secret.get_secret_value(), limits.timeout_turnstile),
        PostgresRateLimiter(db),
        limits,
        settings.salt_seed.get_secret_value().encode(),
        settings.app_env,
    )
    app.state.trace_sinks = []  # extra sinks next to the stream (stored trace, F01-T10)
    # Added before the error middleware, so it runs inside it and its refusal carries the trace id.
    app.add_middleware(BodyLimit, limit=lambda: app.state.limits.max_body_bytes)
    errors.install(app)
    app.include_router(health.router)
    app.include_router(ask.router)
    return app

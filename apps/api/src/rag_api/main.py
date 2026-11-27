"""FastAPI app factory: validates settings, loads the domain and reflects the schema at startup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
import langsmith
import structlog
from fastapi import FastAPI
from rag_core.adapters.alerts import LogAlerter, TelegramAlerter
from rag_core.adapters.defense_log import PostgresDefenseLog
from rag_core.adapters.factory import (
    build_classifier,
    build_embedder,
    build_gateway,
    build_rerankers,
    build_tracer,
    provider_keys,
    rerank_chain,
)
from rag_core.adapters.langsmith_tracer import CLOSE_MIN_S
from rag_core.adapters.pgvector_store import PgVectorStore, check_text_search
from rag_core.adapters.postgres import Database
from rag_core.adapters.rate_limit import PostgresRateLimiter
from rag_core.adapters.trace_store import PostgresTraceStore
from rag_core.adapters.turnstile import TurnstileBotCheck
from rag_core.agent.graph import build_graph
from rag_core.background import BackgroundTasks
from rag_core.domain import check_domain, load_domain, load_examples, load_intents, load_prompts
from rag_core.limits import Limits, load_limits
from rag_core.models_config import config_version, load_models
from rag_core.ports import Alerter
from rag_core.services.gate import Gate
from rag_core.services.gateway import SHUTDOWN_DRAIN_S, ModelGateway
from rag_core.services.guard import CanaryWatch, with_canary
from rag_core.services.redact import Redactor
from rag_core.services.suggestion import Suggestions
from rag_core.telemetry import configure_logging

from rag_api import ask, errors, health
from rag_api.body_limit import BodyLimit
from rag_api.settings import ApiSettings

log = structlog.get_logger()

DEFENSE_DRAIN_S = 5  # at shutdown, how long the pending `defense_events` rows are waited for


async def _warm_rerankers(app: FastAPI) -> None:
    """Load rerank models at startup, so the first visitor after a deploy doesn't wait for it."""
    for level in app.state.rerankers:
        try:
            await anyio.to_thread.run_sync(level.warm)
        except Exception:
            # Not fatal: the rerank chain falls to the next level at question time (ADR 0009).
            log.warning("rerank_warmup_failed", level=type(level).__name__, exc_info=True)


async def _close_traces(app: FastAPI) -> None:
    """The trace writes first (they end LangSmith's last runs), then LangSmith's queue, within one
    `SHUTDOWN_DRAIN_S` in all (and `CANCEL_WAIT_S` for what a stuck write cancels). The tracer always gets
    `CLOSE_MIN_S` (its in-flight wait plus the queue's floor; `close` enforces it), so a stuck write can't
    leave it none."""
    loop = asyncio.get_running_loop()
    ends_at = loop.time() + SHUTDOWN_DRAIN_S
    await app.state.trace_writes.drain(max(0.0, SHUTDOWN_DRAIN_S - CLOSE_MIN_S))
    await anyio.to_thread.run_sync(app.state.tracer.close, ends_at - loop.time())


async def _shut_down(app: FastAPI) -> None:
    """The independent closes, at once: the bot check's client, the gateway's background work (budget
    settles, alerts), the pending defense rows and the traces. Each failure is logged, never stops the rest; the whole takes about
    `SHUTDOWN_DRAIN_S`, within Docker's 10 s."""
    closing = {
        "bot_check": app.state.gate.bot_check.close(),
        "gateway": app.state.gateway.close(),
        "defenses": app.state.defenses.drain(DEFENSE_DRAIN_S),
        "traces": _close_traces(app),
    }
    results = await asyncio.gather(*closing.values(), return_exceptions=True)
    for name, result in zip(closing, results, strict=True):
        if isinstance(result, BaseException):
            log.error("shutdown_failed", part=name, error=type(result).__name__)


async def _warm_suggestions(app: FastAPI) -> None:
    """Embed the example questions at startup; not fatal, `closest` embeds them on first use."""
    try:
        await app.state.suggestions.warm()
    except Exception as exc:
        log.warning("suggestions_warmup_failed", error=type(exc).__name__)


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
    keys = provider_keys(models, settings.provider_keys(), settings.app_env)
    return build_gateway(models, keys, db, _alerter(settings), limits), config_version(models, limits)


def create_app(settings: ApiSettings) -> FastAPI:
    configure_logging(settings.app_env, settings.log_level)
    # The domain package (domain_<name>, ADR 0011) holds what makes the generic core about one
    # person: languages, contact, prompts. DOMAIN selects it.
    domain = load_domain(settings.domain)
    limits = load_limits(settings.limits_file)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as shutdown:  # unwinds last in, first out
            shutdown.push_async_callback(db.close)  # the database closes last, after everything that writes
            shutdown.push_async_callback(_shut_down, app)
            await db.reflect()
            await check_text_search(
                db, domain.languages.values()
            )  # a language Postgres can't search stops it
            # Together: the models load in threads, the examples embed over the network. Unlike the rerank
            # levels, the classifier has no next level: a guard silently off is worse than a failed deploy, so
            # a file that is missing or doesn't match its hash stops the API.
            await asyncio.gather(
                _warm_rerankers(app),
                anyio.to_thread.run_sync(app.state.classifier.warm),
                _warm_suggestions(app),
            )
            yield

    app = FastAPI(title="rag-api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.domain = domain
    app.state.intents = load_intents(settings.domain, domain.document_kinds)
    check_domain(domain, app.state.intents)
    # The canary goes into the prompts once they are loaded and hashed (cross-task decision X5).
    app.state.prompts = with_canary(
        load_prompts(settings.domain, app.state.intents), settings.canary.get_secret_value()
    )
    app.state.canary = settings.canary.get_secret_value()
    app.state.canary_watch = CanaryWatch(app.state.canary)
    # TODO(T10 rebase): hand `app.state.canary` to the shared redactor, so a stored trace never holds it.
    app.state.agent = build_graph()
    app.state.db = db
    app.state.limits = limits
    app.state.embedder = build_embedder(settings)
    app.state.classifier = build_classifier(settings)
    app.state.defenses = PostgresDefenseLog(db)
    # A bad examples.yaml stops the API too.
    app.state.suggestions = Suggestions(load_examples(settings.domain, domain.languages), app.state.embedder)
    app.state.gateway, app.state.model_config_version = _gateway(settings, limits, db)
    # Degraded questions use the local levels only, so they make no model call.
    app.state.local_rerankers = build_rerankers(settings)
    app.state.rerankers = rerank_chain(app.state.gateway, app.state.local_rerankers)
    app.state.vector_store = PgVectorStore(db, limits.rrf_k, limits.search_min_similarity, domain.languages)
    app.state.gate = Gate(
        TurnstileBotCheck(settings.turnstile_secret.get_secret_value(), limits.timeout_turnstile),
        PostgresRateLimiter(db),
        app.state.gateway.event_log,
        limits,
        settings.salt_seed.get_secret_value().encode(),
        settings.app_env,
        app.state.defenses,
    )
    app.state.trace_sinks = []  # extra sinks next to the stream and the recorder, for the tests
    # LangChain's own tracing would send the graph's state unredacted: off, key or not (LLD "Tracing").
    langsmith.configure(enabled=False)
    key = settings.langsmith_api_key
    app.state.tracer = build_tracer(
        key.get_secret_value() if key else None, settings.langsmith_project, settings.langsmith_endpoint
    )
    app.state.trace_store = PostgresTraceStore(db)
    app.state.trace_writes = BackgroundTasks()  # the end-of-request writes, drained at shutdown
    # Everything the stored trace gets passes this first (LLD "Security", trace redaction).
    app.state.redactor = Redactor(domain.pii_allowlist, settings.secret_values())
    # Added before the error middleware, so it runs inside it and its refusal carries the trace id.
    app.add_middleware(BodyLimit, limit=lambda: app.state.limits.max_body_bytes)
    errors.install(app)
    app.include_router(health.router)
    app.include_router(ask.router)
    return app

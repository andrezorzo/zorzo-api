"""Builds the adapters chosen by settings; the apps call these at startup."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, get_args

from pydantic import SecretStr

from rag_core.adapters.postgres import Database
from rag_core.limits import Limits
from rag_core.models_config import ModelsConfig, StageName
from rag_core.ports import Alerter, Embedder, Reranker
from rag_core.services.gateway import ModelGateway
from rag_core.settings import AppSettings

if TYPE_CHECKING:
    from rag_core.adapters.langsmith_tracer import LangSmithTracer


class ConfigError(RuntimeError):
    pass


def _require_local(settings: AppSettings, what: str) -> None:
    if settings.app_env != "local":
        raise ConfigError(f"{what} is accepted only with APP_ENV=local")


def build_embedder(settings: AppSettings) -> Embedder:
    if settings.embedder == "fake":
        _require_local(settings, "EMBEDDER=fake")
        from rag_core.adapters.fake_embedder import HashEmbedder

        return HashEmbedder()
    from rag_core.adapters.ollama import OllamaEmbedder

    return OllamaEmbedder(settings.ollama_url, settings.embed_model_tag)


def build_rerankers(settings: AppSettings) -> list[Reranker]:
    """The rerank chain: the local level (or the pass-through fake); the fused order is the last resort.

    Imports are lazy so ingest, which never reranks, doesn't load onnxruntime.
    """
    if settings.reranker == "fake":
        _require_local(settings, "RERANKER=fake")
        from rag_core.adapters.fake_reranker import FakeReranker

        return [FakeReranker()]
    from rag_core.adapters.local_reranker import LocalReranker

    return [LocalReranker(settings.rerank_cache_dir)]


def provider_keys(
    models: ModelsConfig, keys: Mapping[str, str | None], app_env: str
) -> dict[str, str | None]:
    """Each provider's key; in production a provider a chain uses needs its key."""
    used = {e.provider for name in get_args(StageName) for e in models.stage(name).chain}
    found: dict[str, str | None] = {}
    for name, provider in models.providers.items():
        key = None
        if provider.key_env is not None:
            if provider.key_env not in keys:
                raise ConfigError(f"provider {name}: unknown key variable {provider.key_env}")
            key = keys[provider.key_env]
            if key is None and app_env == "production" and name in used:
                raise ConfigError(f"{provider.key_env} is required in production (provider {name})")
        found[name] = key
    return found


def build_gateway(
    models: ModelsConfig,
    keys: Mapping[str, str | None],
    db: Database,
    alerter: Alerter,
    limits: Limits,
) -> ModelGateway:
    """The model gateway: LiteLLM for the calls (ADR 0016), the Postgres stores for the budget, the alerts and
    the cooldown rows. `keys` maps each provider to its key (`provider_keys`)."""
    from rag_core.adapters.alerts import PostgresAlertStore
    from rag_core.adapters.budget import PostgresBudget
    from rag_core.adapters.event_log import PostgresEventLog
    from rag_core.adapters.litellm_models import LiteLLMModels
    from rag_core.services.alerts import Alerts

    return ModelGateway(
        models,
        LiteLLMModels(models, keys, limits.breaker_cooldown),
        PostgresBudget(db),
        Alerts(PostgresAlertStore(db), alerter, limits),
        PostgresEventLog(db),
        limits,
    )


def rerank_chain(gateway: ModelGateway, local: list[Reranker]) -> list[Reranker]:
    """The remote level when the `rerank` stage has entries, then the local ones (ADR 0009)."""
    from rag_core.services.gateway_reranker import GatewayReranker

    remote: list[Reranker] = [GatewayReranker(gateway)] if gateway.config.stage("rerank").chain else []
    return [*remote, *local]


class LangSmithSettings(Protocol):
    """The settings of an app that can trace to LangSmith."""

    @property
    def langsmith_api_key(self) -> SecretStr | None: ...

    @property
    def langsmith_project(self) -> str | None: ...

    @property
    def langsmith_endpoint(self) -> str | None: ...


def build_tracer(settings: LangSmithSettings) -> "LangSmithTracer | None":
    """LangSmith's tracer, only when a key is set; LangChain's own tracing is off either way (see
    `adapters/langsmith_tracer.py`)."""
    from rag_core.adapters.langsmith_tracer import LangSmithTracer, disable_ambient_tracing

    disable_ambient_tracing()
    if settings.langsmith_api_key is None:
        return None
    return LangSmithTracer(
        settings.langsmith_api_key.get_secret_value(), settings.langsmith_project, settings.langsmith_endpoint
    )

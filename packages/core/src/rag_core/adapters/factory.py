"""Builds the adapters chosen by settings; the apps call these at startup."""

from collections.abc import Mapping

from rag_core.models_config import ModelsConfig
from rag_core.ports import Embedder, ModelProvider, Reranker
from rag_core.settings import AppSettings


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


def build_providers(
    models: ModelsConfig, keys: Mapping[str, str | None], app_env: str
) -> dict[str, ModelProvider]:
    """One client per provider of the models config; in production a provider a chain uses needs its key."""
    from rag_core.adapters.openai_compat import OpenAICompatProvider

    used = {e.provider for name in ("route", "rerank", "answer", "judge") for e in models.stage(name).chain}
    providers: dict[str, ModelProvider] = {}
    for name, provider in models.providers.items():
        key = None
        if provider.key_env is not None:
            if provider.key_env not in keys:
                raise ConfigError(f"provider {name}: unknown key variable {provider.key_env}")
            key = keys[provider.key_env]
            if key is None and app_env == "production" and name in used:
                raise ConfigError(f"{provider.key_env} is required in production (provider {name})")
        providers[name] = OpenAICompatProvider(provider.base_url, key)
    return providers

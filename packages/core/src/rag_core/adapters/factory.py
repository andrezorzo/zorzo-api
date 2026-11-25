"""Builds the adapters chosen by settings; the apps call these at startup."""

from rag_core.ports import Embedder, Reranker
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

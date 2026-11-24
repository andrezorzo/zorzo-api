"""Builds the adapters chosen by settings; the apps call these at startup."""

from pathlib import Path

from rag_core.adapters.fake_embedder import HashEmbedder
from rag_core.adapters.fake_reranker import FakeReranker
from rag_core.adapters.local_reranker import LocalReranker
from rag_core.adapters.ollama import OllamaEmbedder
from rag_core.ports import Embedder, Reranker
from rag_core.settings import AppSettings


class ConfigError(RuntimeError):
    pass


def require_local(settings: AppSettings, what: str) -> None:
    if settings.app_env != "local":
        raise ConfigError(f"{what} is accepted only with APP_ENV=local")


def build_embedder(settings: AppSettings) -> Embedder:
    if settings.embedder == "fake":
        require_local(settings, "EMBEDDER=fake")
        return HashEmbedder()
    return OllamaEmbedder(settings.ollama_url, settings.embed_model_tag)


def build_rerankers(settings: AppSettings, kind: str, cache_dir: Path) -> list[Reranker]:
    """The rerank chain of this task: the local level (or the pass-through fake); `none` is the fallback."""
    if kind == "fake":
        require_local(settings, "RERANKER=fake")
        return [FakeReranker()]
    return [LocalReranker(cache_dir)]

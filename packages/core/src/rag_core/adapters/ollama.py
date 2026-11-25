"""Embeddings from Ollama on the VPS with a pinned tag (ADR 0008)."""

from langchain_ollama import OllamaEmbeddings

TIMEOUT_S = 120


class OllamaEmbedder:
    def __init__(self, base_url: str, tag: str) -> None:
        self._tag = tag
        # A stuck Ollama fails the call instead of hanging it; a long ingest batch still fits.
        self._client = OllamaEmbeddings(model=tag, base_url=base_url, client_kwargs={"timeout": TIMEOUT_S})

    @property
    def tag(self) -> str:
        return self._tag

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)

"""Embeddings from Ollama on the VPS with a pinned tag (ADR 0008)."""

from langchain_ollama import OllamaEmbeddings


class OllamaEmbedder:
    def __init__(self, base_url: str, tag: str) -> None:
        self._tag = tag
        self._client = OllamaEmbeddings(model=tag, base_url=base_url)

    @property
    def tag(self) -> str:
        return self._tag

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)

"""The suggestion on an abstention or decline (LLD "6.3 Agent run"): the example question closest to the
visitor's, by embedding similarity. One embedding call and no model call."""

import asyncio
from collections.abc import Sequence

import numpy as np

from rag_core.domain import Example
from rag_core.ports import Embedder


class Suggestions:
    def __init__(self, examples: Sequence[Example], embedder: Embedder) -> None:
        self.examples = list(examples)
        self.embedder = embedder
        self._vectors: np.ndarray | None = None  # one row per example, unit length

    async def warm(self) -> None:
        """Embed the examples; may raise, and `closest` tries again."""
        if self._vectors is None and self.examples:
            self._vectors = _unit(await self.embedder.embed([e.text for e in self.examples]))

    async def closest(self, text: str, language: str, deadline_at: float) -> str | None:
        """The id of the closest example in `language`, the first in the file on a tie; None when the
        language has no example. Raises on an embedder failure or when the deadline passes."""
        indexes = [i for i, e in enumerate(self.examples) if e.lang == language]
        if not indexes:
            return None
        async with asyncio.timeout_at(deadline_at):
            await self.warm()
            assert self._vectors is not None  # warm() set it: there are examples
            query = _unit(await self.embedder.embed([text]))[0]
        similarity = self._vectors[indexes] @ query
        return self.examples[indexes[int(np.argmax(similarity))]].id


def _unit(vectors: list[list[float]]) -> "np.ndarray":
    matrix = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)

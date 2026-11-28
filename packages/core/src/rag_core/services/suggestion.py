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
        # Per language: the example indexes and their embeddings (one unit-length row each).
        self._by_language: dict[str, tuple[list[int], np.ndarray]] | None = None
        self._warming = asyncio.Lock()  # one embedding of the examples at a time

    async def warm(self) -> None:
        """Embed the examples, once however many calls come at the same time; may raise, and `closest`
        tries again."""
        async with self._warming:
            if self._by_language is not None or not self.examples:
                return
            vectors = _unit(await self.embedder.embed([e.text for e in self.examples]))
            languages = dict.fromkeys(e.lang for e in self.examples)
            indexes = {lang: [i for i, e in enumerate(self.examples) if e.lang == lang] for lang in languages}
            self._by_language = {lang: (rows, vectors[rows]) for lang, rows in indexes.items()}

    async def closest(
        self, text: str, language: str, deadline_at: float, vector: list[float] | None = None
    ) -> str | None:
        """The id of the closest example in `language`, the first in the file on a tie; None when the
        language has no example. `vector` is the text's embedding when the run already has it. Raises on an
        embedder failure or when the deadline passes."""
        if not any(e.lang == language for e in self.examples):
            return None
        async with asyncio.timeout_at(deadline_at):
            await self.warm()
            query = _unit([vector] if vector is not None else await self.embedder.embed([text]))[0]
        assert self._by_language is not None  # warm() set it: the language has an example
        indexes, matrix = self._by_language[language]
        return self.examples[indexes[int(np.argmax(matrix @ query))]].id


def _unit(vectors: list[list[float]]) -> "np.ndarray":
    matrix = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)

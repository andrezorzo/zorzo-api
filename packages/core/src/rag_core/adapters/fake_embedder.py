"""Deterministic embedder for tests and local runs without Ollama (`EMBEDDER=fake`, local only).

Each word is hashed to one of 1024 dimensions, so texts with words in common have nearby vectors.
"""

import hashlib
import math
import re

from rag_core.ports import EMBEDDING_DIM as DIM

WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbedder:
    tag = "fake-hash-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        for word in WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIM] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:  # pgvector can't compare a zero vector by cosine
            vector[0], norm = 1.0, 1.0
        return [v / norm for v in vector]

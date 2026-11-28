"""Local rerank level (ADR 0009): a small multilingual cross-encoder, ONNX on CPU.

fastembed has no built-in copy of this model, so it is registered as a custom model from its Hugging Face
repository (Apache-2.0). The API image bakes the files into `RERANK_CACHE_DIR` and loads them offline.
"""

import functools
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio

from rag_core.adapters import onnx_model as shared
from rag_core.adapters.onnx_model import ModelIntegrityError
from rag_core.ports import SearchHit

__all__ = ["LocalReranker", "ModelIntegrityError", "verify_model_files"]

MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_FILE = "onnx/model_quint8_avx2.onnx"
MODEL_REVISION = (
    "1427fd652930e4ba29e8149678df786c240d8825"  # the repository commit the hashes below come from
)
# SHA-256 of every file the model loads (the ONNX hash equals Hugging Face's LFS id). The files are downloaded
# at the pinned revision and checked before onnxruntime or the tokenizer read them: a changed file is refused,
# never parsed.
MODEL_FILES_SHA256 = {
    "onnx/model_quint8_avx2.onnx": "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
    "tokenizer.json": "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
    "tokenizer_config.json": "e7fbfbfa6347b4e414c1cee50d142e2c2f9a895dad68b068ae83a8b564c3837e",
    "config.json": "cc2cfe51aa3fd759d21d21acf5dfd6994aa67a3c9210636d22e143699d336c77",
    "special_tokens_map.json": "378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d",
}


RERANK_SLOTS = 2


def verify_model_files(snapshot: Path) -> None:
    """Raise unless this exact snapshot folder holds the expected files."""
    shared.verify_model_files(snapshot, MODEL_FILES_SHA256, "rerank")


@functools.cache
def _cross_encoder_class() -> Any:
    """fastembed's cross-encoder with this model registered as a custom one, once."""
    shared.import_onnxruntime()
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    TextCrossEncoder.add_custom_model(
        model=MODEL, model_file=MODEL_FILE, sources=ModelSource(hf=MODEL), size_in_gb=0.13
    )
    return TextCrossEncoder


class LocalReranker(shared.LazyModel[Any]):
    def __init__(self, cache_dir: Path) -> None:
        super().__init__(cache_dir, RERANK_SLOTS)

    def _build(self) -> Any:
        snapshot = shared.download_snapshot(MODEL, MODEL_REVISION, list(MODEL_FILES_SHA256), self.cache_dir)
        verify_model_files(snapshot)
        return _cross_encoder_class()(MODEL, cache_dir=str(self.cache_dir), specific_model_path=str(snapshot))

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        # The cross-encoder is CPU-bound: keep it off the event loop (LLD "Process model").
        # abandon_on_cancel: the request deadline ends the wait even if the thread can't be stopped.
        return await anyio.to_thread.run_sync(self._rerank, query, hits, abandon_on_cancel=True)

    def _rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        with self.slot():
            scores = list(self.model().rerank(query, [h.content for h in hits]))
        scored = [replace(h, score=float(s)) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)

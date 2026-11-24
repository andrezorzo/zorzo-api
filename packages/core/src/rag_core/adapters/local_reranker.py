"""Local rerank level (ADR 0009): a small multilingual cross-encoder, ONNX on CPU.

fastembed has no built-in copy of this model, so it is registered as a custom model from its Hugging Face
repository (Apache-2.0). The API image bakes the files into `RERANK_CACHE_DIR` and loads them offline.
"""

import functools
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

from rag_core.ports import SearchHit

MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_FILE = "onnx/model_quint8_avx2.onnx"
# SHA-256 of every file the model loads, checked after download (repository revision 1427fd65, the ONNX
# hash equal to Hugging Face's LFS id). A changed file means a changed model: it is refused, not run.
MODEL_FILES_SHA256 = {
    "onnx/model_quint8_avx2.onnx": "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
    "tokenizer.json": "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626",
    "tokenizer_config.json": "e7fbfbfa6347b4e414c1cee50d142e2c2f9a895dad68b068ae83a8b564c3837e",
    "config.json": "cc2cfe51aa3fd759d21d21acf5dfd6994aa67a3c9210636d22e143699d336c77",
    "special_tokens_map.json": "378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d",
}


RETRY_AFTER_S = 60


class ModelIntegrityError(RuntimeError):
    pass


def verify_model_files(cache_dir: Path) -> None:
    """Raise unless one downloaded snapshot of the model has exactly the expected files."""
    for snapshot in cache_dir.glob(f"models--{MODEL.replace('/', '--')}/snapshots/*"):
        if all(
            (snapshot / name).is_file()
            and hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == digest
            for name, digest in MODEL_FILES_SHA256.items()
        ):
            return
    raise ModelIntegrityError(f"the rerank model files in {cache_dir} don't match the pinned hashes")


@functools.cache
def _cross_encoder_class() -> Any:
    """Imports onnxruntime with its telemetry off and registers the model, once.

    onnxruntime sends usage telemetry to Microsoft by default; nothing about a visitor's question may leave
    the VPS through it. The variable is read when onnxruntime starts, so the import happens here, after it
    is set (the image and the CI set it too).
    """
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    import onnxruntime
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    onnxruntime.disable_telemetry_events()
    TextCrossEncoder.add_custom_model(
        model=MODEL, model_file=MODEL_FILE, sources=ModelSource(hf=MODEL), size_in_gb=0.13
    )
    return TextCrossEncoder


class LocalReranker:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._model: Any = None
        self._load = threading.Lock()
        self._failed_at: float | None = None

    def model(self) -> Any:
        with self._load:
            if self._model is None:
                # After a failed load, questions fall to the next level at once instead of queueing behind a
                # new download attempt; it is tried again after a cooldown.
                if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER_S:
                    raise RuntimeError("the rerank model failed to load recently")
                try:
                    model = _cross_encoder_class()(MODEL, cache_dir=str(self.cache_dir))
                    verify_model_files(self.cache_dir)
                except Exception:
                    self._failed_at = time.monotonic()
                    raise
                self._model = model
            return self._model

    def warm(self) -> None:
        self.model()

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        scores = list(self.model().rerank(query, [h.content for h in hits]))
        scored = [SearchHit(h.id, h.title, h.content, float(s)) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)

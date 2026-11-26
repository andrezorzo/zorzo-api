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

import anyio

from rag_core.ports import SearchHit

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


RETRY_AFTER_S = 60
LOCK_WAIT_S = 0.5
RERANK_SLOTS = 2


class ModelIntegrityError(RuntimeError):
    pass


def verify_model_files(snapshot: Path) -> None:
    """Raise unless this exact snapshot folder holds the expected files."""
    for name, digest in MODEL_FILES_SHA256.items():
        path = snapshot / name
        if not path.is_file():
            raise ModelIntegrityError(f"rerank model file {name} is missing from {snapshot}")
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()  # in chunks: the ONNX file is 130 MB
        if actual != digest:
            raise ModelIntegrityError(f"rerank model file {name} in {snapshot} doesn't match its pinned hash")


def _download(cache_dir: Path) -> Path:
    """The pinned revision's files, from the cache when present (the image loads offline)."""
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            MODEL, revision=MODEL_REVISION, allow_patterns=list(MODEL_FILES_SHA256), cache_dir=str(cache_dir)
        )
    )


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
        # Rerank threads the request deadline abandoned keep running; this cap still counts them, so they
        # can't pile up and starve the CPU. A question finding no free slot falls to the next level.
        self._slots = threading.BoundedSemaphore(RERANK_SLOTS)

    def model(self) -> Any:
        # Another thread loading the model means this question falls to the next level now, not later.
        if not self._load.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("the rerank model is still loading")
        try:
            if self._model is None:
                # After a failed load, questions fall to the next level at once instead of queueing behind a
                # new download attempt; it is tried again after a cooldown.
                if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER_S:
                    raise RuntimeError("the rerank model failed to load recently")
                try:
                    snapshot = _download(self.cache_dir)
                    verify_model_files(snapshot)
                    self._model = _cross_encoder_class()(
                        MODEL, cache_dir=str(self.cache_dir), specific_model_path=str(snapshot)
                    )
                except Exception:
                    self._failed_at = time.monotonic()
                    raise
            return self._model
        finally:
            self._load.release()

    def warm(self) -> None:
        self.model()

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        # The cross-encoder is CPU-bound: keep it off the event loop (LLD "Process model").
        # abandon_on_cancel: the request deadline ends the wait even if the thread can't be stopped.
        return await anyio.to_thread.run_sync(self._rerank, query, hits, abandon_on_cancel=True)

    def _rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if not self._slots.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("every rerank slot is busy")
        try:
            scores = list(self.model().rerank(query, [h.content for h in hits]))
        finally:
            self._slots.release()
        scored = [SearchHit(h.id, h.title, h.content, float(s)) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)

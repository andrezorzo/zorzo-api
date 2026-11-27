"""What the local ONNX models share (the reranker and the injection classifier): pinned files checked by
SHA-256 before anything reads them, onnxruntime with its telemetry off, and a lazy load that never piles up.
"""

import hashlib
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

RETRY_AFTER_S = 60
LOCK_WAIT_S = 0.5


class ModelIntegrityError(RuntimeError):
    pass


def verify_model_files(snapshot: Path, hashes: Mapping[str, str], label: str) -> None:
    """Raise unless this exact snapshot folder holds the files with these SHA-256 digests."""
    for name, digest in hashes.items():
        path = snapshot / name
        if not path.is_file():
            raise ModelIntegrityError(f"{label} model file {name} is missing from {snapshot}")
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()  # in chunks: a model file may be 1.1 GB
        if actual != digest:
            raise ModelIntegrityError(
                f"{label} model file {name} in {snapshot} doesn't match its pinned hash"
            )


def download_snapshot(repo: str, revision: str, files: list[str], cache_dir: Path) -> Path:
    """The pinned revision's files, from the cache when present (the image loads offline)."""
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo, revision=revision, allow_patterns=files, cache_dir=str(cache_dir)))


def import_onnxruntime() -> Any:
    """onnxruntime with its usage telemetry off: nothing about a visitor's question may leave the VPS through
    it. The variable is read when onnxruntime starts, so the import happens here, after it is set (the image
    and the CI set it too)."""
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    import onnxruntime

    onnxruntime.disable_telemetry_events()
    return onnxruntime


class LazyModel[M]:
    """A model loaded on first use, from files `_build` downloads and verifies.

    Another thread loading it means this call fails now, not later; after a failed load, calls fail at once
    instead of queueing behind a new download attempt, which is tried again after a cooldown. Work slots cap
    the threads running the model, so a call the request deadline abandoned can't pile up with others.
    """

    def __init__(self, cache_dir: Path, slots: int) -> None:
        self.cache_dir = cache_dir
        self._model: M | None = None
        self._load = threading.Lock()
        self._failed_at: float | None = None
        self._slots = threading.BoundedSemaphore(slots)

    def _build(self) -> M:
        raise NotImplementedError

    def model(self) -> M:
        if not self._load.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("the model is still loading")
        try:
            if self._model is None:
                if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER_S:
                    raise RuntimeError("the model failed to load recently")
                try:
                    self._model = self._build()
                except Exception:
                    self._failed_at = time.monotonic()
                    raise
            return self._model
        finally:
            self._load.release()

    def warm(self) -> None:
        self.model()

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._slots.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("every model slot is busy")
        try:
            yield
        finally:
            self._slots.release()

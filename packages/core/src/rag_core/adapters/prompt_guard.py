"""Guard layer L2: Llama Prompt Guard 2 (86M), exported to ONNX, on CPU (LLD "Libraries").

Runs the way the local reranker does: files from a pinned revision, checked by SHA-256 before onnxruntime or
the tokenizer read them, and loaded offline from `GUARD_CACHE_DIR` in the image.
Labels: 0 benign, 1 malicious.
"""

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

import anyio
import numpy as np

from rag_core.adapters.local_reranker import ModelIntegrityError

MODEL = "gravitee-io/Llama-Prompt-Guard-2-86M-onnx"
MODEL_FILE = "model.onnx"
MODEL_REVISION = "45a05fbd5337a864edc608f994911f009c37ca57"
MODEL_FILES_SHA256 = {
    "model.onnx": "fbe0be6a471873b6c52f7d6631c16fbddb88ba8c7ab2ba34ca48e8e77ffd9999",
    "tokenizer.json": "870798f0e3bb05c636bf62be904b2d8f48ace4785e9c1d71a8a67bd0586c941d",
    "tokenizer_config.json": "d5cff0d149f1084504aa8cbdd941289a011ec7f027519feb49e397c58b564398",
    "config.json": "a39ae60b9b718b72bbe3f359ad07ddf6909dcb09ccff936c00a488ceb4e983a6",
    "special_tokens_map.json": "b2f1b2f15f29a6b6d9d6ea4eca1675d2c231a71477f151d48f79cc83a625ba21",
}
MAX_TOKENS = 512  # the model's `max_position_embeddings`; a question is far shorter
MALICIOUS = 1  # the label index of `id2label`
LOCK_WAIT_S = 0.5
GUARD_SLOTS = 2


def verify_model_files(snapshot: Path) -> None:
    """Raise unless this exact snapshot folder holds the expected files."""
    for name, digest in MODEL_FILES_SHA256.items():
        path = snapshot / name
        if not path.is_file():
            raise ModelIntegrityError(f"guard model file {name} is missing from {snapshot}")
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()  # in chunks: the ONNX file is 1.1 GB
        if actual != digest:
            raise ModelIntegrityError(f"guard model file {name} in {snapshot} doesn't match its pinned hash")


def _download(cache_dir: Path) -> Path:
    """The pinned revision's files, from the cache when present (the image loads offline)."""
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            MODEL, revision=MODEL_REVISION, allow_patterns=list(MODEL_FILES_SHA256), cache_dir=str(cache_dir)
        )
    )


def softmax_malicious(logits: Any) -> float:
    """The malicious label's probability from the model's two logits."""
    row = np.asarray(logits, dtype=np.float64).reshape(-1)
    exp = np.exp(row - row.max())  # the max keeps the exponentials from overflowing
    return float(exp[MALICIOUS] / exp.sum())


class PromptGuardClassifier:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._model: tuple[Any, Any] | None = None  # (tokenizer, ONNX session)
        self._load = threading.Lock()
        # A scoring thread the request deadline abandoned keeps running; this cap still counts it.
        self._slots = threading.BoundedSemaphore(GUARD_SLOTS)

    def model(self) -> tuple[Any, Any]:
        with self._load:
            if self._model is None:
                snapshot = _download(self.cache_dir)
                verify_model_files(snapshot)
                self._model = _open(snapshot)
            return self._model

    def warm(self) -> None:
        self.model()

    async def score(self, text: str) -> float:
        # CPU-bound: keep it off the event loop; the request deadline ends the wait.
        return await anyio.to_thread.run_sync(self._score, text, abandon_on_cancel=True)

    def _score(self, text: str) -> float:
        if not self._slots.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("every guard slot is busy")
        try:
            tokenizer, session = self.model()
            encoded = tokenizer.encode(text)
            inputs = {
                "input_ids": np.asarray([encoded.ids], dtype=np.int64),
                "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
            }
            logits = session.run(None, inputs)[0]
        finally:
            self._slots.release()
        return softmax_malicious(logits)


def _open(snapshot: Path) -> tuple[Any, Any]:
    """The tokenizer and the ONNX session, with onnxruntime's telemetry off (as the reranker does)."""
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    import onnxruntime
    from tokenizers import Tokenizer

    onnxruntime.disable_telemetry_events()
    tokenizer = Tokenizer.from_file(str(snapshot / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    session = onnxruntime.InferenceSession(str(snapshot / MODEL_FILE), providers=["CPUExecutionProvider"])
    return tokenizer, session

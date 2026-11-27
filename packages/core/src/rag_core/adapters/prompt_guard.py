"""Guard layer L2: Llama Prompt Guard 2 (86M), exported to ONNX, on CPU (LLD "Libraries").

Runs the way the local reranker does: files from a pinned revision, checked by SHA-256 before onnxruntime or
the tokenizer read them, and loaded offline from `GUARD_CACHE_DIR` in the image.
Labels: 0 benign, 1 malicious.
"""

import os
from pathlib import Path
from typing import Any

import anyio
import numpy as np

from rag_core.adapters import onnx_model as shared

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
GUARD_SLOTS = 2


def softmax_malicious(logits: Any) -> float:
    """The malicious label's probability from the model's two logits."""
    row = np.asarray(logits, dtype=np.float64).reshape(-1)
    exp = np.exp(row - row.max())  # the max keeps the exponentials from overflowing
    return float(exp[MALICIOUS] / exp.sum())


class PromptGuardClassifier(shared.LazyModel[tuple[Any, Any]]):
    """The model is a (tokenizer, ONNX session) pair."""

    def __init__(self, cache_dir: Path) -> None:
        super().__init__(cache_dir, GUARD_SLOTS)

    def _build(self) -> tuple[Any, Any]:
        snapshot = shared.download_snapshot(MODEL, MODEL_REVISION, list(MODEL_FILES_SHA256), self.cache_dir)
        shared.verify_model_files(snapshot, MODEL_FILES_SHA256, "guard")
        return _open(snapshot)

    async def score(self, text: str) -> float:
        # CPU-bound: keep it off the event loop; the request deadline ends the wait.
        return await anyio.to_thread.run_sync(self._score, text, abandon_on_cancel=True)

    def _score(self, text: str) -> float:
        with self.slot():
            tokenizer, session = self.model()
            encoded = tokenizer.encode(text)
            inputs = {
                "input_ids": np.asarray([encoded.ids], dtype=np.int64),
                "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
            }
            logits = session.run(None, inputs)[0]
        return softmax_malicious(logits)


def _open(snapshot: Path) -> tuple[Any, Any]:
    """The tokenizer and the ONNX session; the CPU is shared between the scoring slots, not oversubscribed."""
    from tokenizers import Tokenizer

    onnxruntime = shared.import_onnxruntime()
    tokenizer = Tokenizer.from_file(str(snapshot / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = max(1, (os.cpu_count() or 1) // GUARD_SLOTS)
    session = onnxruntime.InferenceSession(
        str(snapshot / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return tokenizer, session

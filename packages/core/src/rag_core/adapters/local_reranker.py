"""Local rerank level (ADR 0009): a small multilingual cross-encoder, ONNX on CPU.

fastembed has no built-in copy of this model, so it is registered as a custom model from its Hugging Face
repository (Apache-2.0). The API image bakes the files into `RERANK_CACHE_DIR` and loads them offline.
"""

import os
import threading
from pathlib import Path

# onnxruntime sends usage telemetry to Microsoft by default; nothing about a visitor's question may leave
# the VPS through it. The variable must be set before onnxruntime starts.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

import onnxruntime
from fastembed.common.model_description import ModelSource
from fastembed.rerank.cross_encoder import TextCrossEncoder

from rag_core.ports import SearchHit

onnxruntime.disable_telemetry_events()

MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_FILE = "onnx/model_quint8_avx2.onnx"

_registered = threading.Lock()
_is_registered = False


def _register() -> None:
    global _is_registered
    with _registered:
        if not _is_registered:
            TextCrossEncoder.add_custom_model(
                model=MODEL, model_file=MODEL_FILE, sources=ModelSource(hf=MODEL), size_in_gb=0.13
            )
            _is_registered = True


class LocalReranker:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._model: TextCrossEncoder | None = None
        self._load = threading.Lock()

    def model(self) -> TextCrossEncoder:
        with self._load:
            if self._model is None:
                _register()
                self._model = TextCrossEncoder(MODEL, cache_dir=str(self.cache_dir))
            return self._model

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        scores = list(self.model().rerank(query, [h.content for h in hits]))
        scored = [SearchHit(h.id, h.title, h.content, float(s)) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)

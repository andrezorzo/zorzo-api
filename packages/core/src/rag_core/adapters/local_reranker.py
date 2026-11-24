"""Local rerank level (ADR 0009): a small multilingual cross-encoder, ONNX on CPU.

fastembed has no built-in copy of this model, so it is registered as a custom model from its Hugging Face
repository (Apache-2.0). The API image bakes the files into `RERANK_CACHE_DIR` and loads them offline.
"""

import functools
import os
import threading
from pathlib import Path
from typing import Any

from rag_core.ports import SearchHit

MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_FILE = "onnx/model_quint8_avx2.onnx"


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

    def model(self) -> Any:
        with self._load:
            if self._model is None:
                self._model = _cross_encoder_class()(MODEL, cache_dir=str(self.cache_dir))
            return self._model

    def warm(self) -> None:
        self.model()

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        scores = list(self.model().rerank(query, [h.content for h in hits]))
        scored = [SearchHit(h.id, h.title, h.content, float(s)) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)

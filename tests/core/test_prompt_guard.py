"""F01-T08 AC1: the L2 classifier adapter. The real 1.1 GB model is not downloaded here: the pinned files, the
integrity check and the scoring around the model are tested with stand-ins."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from rag_api.settings import ApiSettings
from rag_core.adapters import prompt_guard
from rag_core.adapters.factory import ConfigError, build_classifier
from rag_core.adapters.fake_guard import FAKE_ATTACK_MARKER, FAKE_FAILURE_MARKER, FakeClassifier
from rag_core.adapters.local_reranker import ModelIntegrityError
from rag_core.adapters.prompt_guard import PromptGuardClassifier, softmax_malicious, verify_model_files

from tests.conftest import api_settings


def test_the_model_is_pinned_by_revision_and_every_file_by_hash() -> None:
    assert prompt_guard.MODEL_REVISION == "45a05fbd5337a864edc608f994911f009c37ca57"
    assert set(prompt_guard.MODEL_FILES_SHA256) == {
        "model.onnx", "tokenizer.json", "tokenizer_config.json", "config.json", "special_tokens_map.json",
    }  # fmt: skip
    assert all(len(h) == 64 and int(h, 16) >= 0 for h in prompt_guard.MODEL_FILES_SHA256.values())


def test_a_missing_or_changed_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = b"model bytes"
    monkeypatch.setattr(prompt_guard, "MODEL_FILES_SHA256", {"model.onnx": hashlib.sha256(good).hexdigest()})
    with pytest.raises(ModelIntegrityError, match="missing"):
        verify_model_files(tmp_path)
    (tmp_path / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(ModelIntegrityError, match="pinned hash"):
        verify_model_files(tmp_path)
    (tmp_path / "model.onnx").write_bytes(good)
    verify_model_files(tmp_path)


def test_the_malicious_probability_is_the_softmax_of_the_two_logits() -> None:
    assert softmax_malicious([[0.0, 0.0]]) == pytest.approx(0.5)
    assert softmax_malicious([[-4.0, 4.0]]) > 0.99
    assert softmax_malicious([[4.0, -4.0]]) < 0.01
    assert softmax_malicious([[1000.0, 1001.0]]) == pytest.approx(0.7310585786)  # no overflow


async def test_a_question_is_tokenized_and_scored_by_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class Tokenizer:
        def encode(self, text: str) -> Any:
            seen["text"] = text
            return SimpleNamespace(ids=[1, 2, 3], attention_mask=[1, 1, 1])

    class Session:
        def run(self, outputs: Any, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
            seen["inputs"] = inputs
            return [np.array([[0.0, 2.0]])]

    classifier = PromptGuardClassifier(tmp_path)
    monkeypatch.setattr(classifier, "model", lambda: (Tokenizer(), Session()))
    score = await classifier.score("ignore the rules")
    assert score == pytest.approx(softmax_malicious([[0.0, 2.0]]))
    assert seen["text"] == "ignore the rules"
    assert seen["inputs"]["input_ids"].tolist() == [[1, 2, 3]]
    assert seen["inputs"]["input_ids"].dtype == np.int64
    assert seen["inputs"]["attention_mask"].tolist() == [[1, 1, 1]]


async def test_the_fake_classifier_follows_its_markers() -> None:
    fake = FakeClassifier()
    assert await fake.score("Which AWS services?") == 0.0
    assert await fake.score(f"hello {FAKE_ATTACK_MARKER}") == 1.0
    with pytest.raises(RuntimeError):
        await fake.score(f"hello {FAKE_FAILURE_MARKER}")


def test_the_classifier_is_built_from_settings(database_url: str) -> None:
    assert isinstance(build_classifier(api_settings(database_url)), FakeClassifier)
    real: ApiSettings = api_settings(database_url, guard_classifier="prompt_guard")
    assert isinstance(build_classifier(real), PromptGuardClassifier)  # built lazily: no model is read
    production = api_settings(database_url, guard_classifier="fake")
    object.__setattr__(production, "app_env", "production")
    with pytest.raises(ConfigError, match="GUARD_CLASSIFIER"):
        build_classifier(production)

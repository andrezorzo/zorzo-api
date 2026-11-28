"""F01-T09 AC3: the local language detector, offline."""

import socket
from collections.abc import Iterator

import pytest
from rag_core.adapters.language import FastTextLanguageDetector

QUESTIONS = {
    "en": ["Which AWS services has he used?", "Where did he work before?"],
    "pt": ["Quais serviços da AWS ele já usou?", "Onde ele trabalhou antes?"],
    "es": ["¿Qué servicios de AWS ha usado él?", "¿Dónde trabajó antes de esto?"],
}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the detector opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    yield


def test_detects_the_domains_languages_offline(offline: None) -> None:
    detector = FastTextLanguageDetector()
    detector.warm()
    for expected, questions in QUESTIONS.items():
        assert [detector.detect(q) for q in questions] == [expected] * len(questions)


def test_a_text_with_a_line_break_is_still_read(offline: None) -> None:
    assert FastTextLanguageDetector().detect("Where did he\nwork before?") == "en"

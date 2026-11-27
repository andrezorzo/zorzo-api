"""Deterministic injection classifier for tests and local runs (`GUARD_CLASSIFIER=fake`, local only)."""

FAKE_ATTACK_MARKER = "FAKE-ATTACK"  # a question holding it scores 1.0
FAKE_FAILURE_MARKER = "FAKE-CLASSIFIER-FAILURE"  # a question holding it makes the classifier raise


class FakeClassifier:
    def warm(self) -> None:
        pass

    async def score(self, text: str) -> float:
        if FAKE_FAILURE_MARKER in text:
            raise RuntimeError("the fake classifier was told to fail")
        return 1.0 if FAKE_ATTACK_MARKER in text else 0.0

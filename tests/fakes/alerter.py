"""An Alerter that keeps what it is asked to send, or fails (`fail=True`) like a Telegram outage."""

from rag_core.ports import AlertKind


class RecordingAlerter:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send(self, kind: AlertKind, message: str) -> None:
        if self.fail:
            raise RuntimeError("the alerter is down")
        self.sent.append((kind, message))

    async def close(self) -> None:
        pass

"""Trace sinks (LLD "Ports", TraceSink): one step event goes to every sink."""

from rag_core.events import StreamEvent
from rag_core.ports import TraceSink


class FanOutSink:
    def __init__(self, sinks: list[TraceSink]) -> None:
        self.sinks = sinks

    def record(self, event: StreamEvent) -> None:
        for sink in self.sinks:
            sink.record(event)

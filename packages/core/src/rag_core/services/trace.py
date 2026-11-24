"""Trace sinks (LLD "Ports", TraceSink): one step event goes to every sink."""

import structlog

from rag_core.events import StreamEvent
from rag_core.ports import TraceSink

log = structlog.get_logger()


class FanOutSink:
    """The first sink is the visitor's stream; a failing extra sink is logged and skipped, never fatal."""

    def __init__(self, sinks: list[TraceSink]) -> None:
        self.sinks = sinks

    def record(self, event: StreamEvent) -> None:
        primary, *extra = self.sinks
        primary.record(event)
        for sink in extra:
            try:
                sink.record(event)
            except Exception:
                log.warning("trace_sink_failed", sink=type(sink).__name__, kind=event.kind, exc_info=True)

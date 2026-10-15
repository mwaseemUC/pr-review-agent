from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

AUDITED_EVENTS = ("user.created", "user.deleted", "role.granted", "role.revoked")


class AuditSink(Protocol):
    def record(self, event: str, payload: dict[str, Any]) -> None: ...


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(event, []):
            try:
                handler(payload)
            except Exception:
                logger.exception("handler for %s failed", event)


def register_audit_handlers(
    bus: EventBus, sink: AuditSink, events: Iterable[str] = AUDITED_EVENTS
) -> None:
    for event in events:
        bus.subscribe(event, lambda payload: sink.record(event, payload))

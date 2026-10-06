"""In-process publish/subscribe for SSE. One process, one bus - hence exactly one uvicorn worker."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class Event:
    topic: str              # "<module>.<what>", e.g. "dashboard.sample"
    data: Any               # must be JSON-serializable (dict, list, model_dump(by_alias=True))


class EventBus:
    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, topic: str, data: Any) -> None:
        """Non-blocking. A slow client loses its oldest events instead of stalling the publisher."""
        ev = Event(topic, data)
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()          # drop oldest: live data is only worth its latest value
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)

    @contextmanager
    def subscription(self) -> Iterator[asyncio.Queue[Event]]:
        """Hand out a plain queue: q.get() can be cancelled by a timeout without ending the subscription."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)    # runs on client disconnect

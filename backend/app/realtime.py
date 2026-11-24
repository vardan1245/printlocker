"""In-process pub/sub for live job status over WebSocket.

Services run in worker threads (sync SQLAlchemy), so they publish through
`publish_threadsafe`, which hands the message to the event loop.
Single-process only; with several backend instances this becomes Redis pub/sub.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict


class Hub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        self._subs[user_id].discard(q)
        if not self._subs[user_id]:
            self._subs.pop(user_id, None)

    def _deliver(self, user_id: int, message: dict) -> None:
        for q in list(self._subs.get(user_id, ())):
            if q.full():  # slow client: drop the oldest message
                q.get_nowait()
            q.put_nowait(message)

    def publish_threadsafe(self, user_id: int, message: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._deliver, user_id, message)


hub = Hub()

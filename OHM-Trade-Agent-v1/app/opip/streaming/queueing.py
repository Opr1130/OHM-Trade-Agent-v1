"""Bounded FIFO ingress queue with DROP_RAW_NEWEST semantics."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.opip.streaming.adapter import QueuedRawFrame


@dataclass(frozen=True)
class QueueSnapshot:
    depth: int
    capacity: int
    high_watermark: int
    accepted: int
    dropped_newest: int

    @property
    def utilization_pct(self) -> float:
        if self.capacity <= 0:
            return 0.0
        return round(100.0 * self.depth / self.capacity, 6)


class DropNewestQueue:
    """Bounded queue that never evicts previously accepted evidence."""

    def __init__(self, *, maxsize: int = 5000) -> None:
        if int(maxsize) <= 0:
            raise ValueError("maxsize must be positive")
        self._queue: asyncio.Queue[QueuedRawFrame] = asyncio.Queue(maxsize=int(maxsize))
        self._high_watermark = 0
        self._accepted = 0
        self._dropped_newest = 0

    @property
    def maxsize(self) -> int:
        return self._queue.maxsize

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def offer(self, frame: QueuedRawFrame) -> bool:
        """Enqueue if capacity remains; otherwise drop the incoming frame."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._dropped_newest += 1
            return False
        self._accepted += 1
        self._high_watermark = max(self._high_watermark, self._queue.qsize())
        return True

    async def get(self) -> QueuedRawFrame:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    def discard_all(self) -> int:
        """Discard remaining accepted frames during bounded shutdown."""
        count = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            count += 1
        return count

    def snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            depth=self._queue.qsize(),
            capacity=self._queue.maxsize,
            high_watermark=self._high_watermark,
            accepted=self._accepted,
            dropped_newest=self._dropped_newest,
        )

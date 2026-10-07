import asyncio
from typing import Any

from app.metrics import WS_CLIENTS, WS_DROPPED

Message = dict[str, Any]


class Subscription:
    def __init__(self, max_queue: int) -> None:
        self._queue: asyncio.Queue[Message | None] = asyncio.Queue(maxsize=max_queue)
        self.dropped = False

    async def next(self) -> Message | None:
        """Next message, or None once the subscription has been dropped."""
        return await self._queue.get()

    def _offer(self, message: Message) -> bool:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True

    def _drop(self) -> None:
        self.dropped = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)


class Hub:
    """Fans messages out to WebSocket subscribers. Must only be used from the event loop.

    Each subscriber has a bounded queue. One that falls behind is dropped rather than
    allowed to grow without limit or to delay the others; its socket is closed and the
    browser reconnects and receives a fresh snapshot.
    """

    def __init__(self, max_queue: int = 1000) -> None:
        self._max_queue = max_queue
        self._subscriptions: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        subscription = Subscription(self._max_queue)
        self._subscriptions.add(subscription)
        WS_CLIENTS.set(len(self._subscriptions))
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.discard(subscription)
        WS_CLIENTS.set(len(self._subscriptions))

    def publish(self, message: Message) -> None:
        for subscription in list(self._subscriptions):
            if not subscription._offer(message):
                subscription._drop()
                self.unsubscribe(subscription)
                WS_DROPPED.inc()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

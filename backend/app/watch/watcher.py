import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from app.kube.reader import ClusterReader, KubeObject, ResourceVersionExpired
from app.metrics import WATCH_EVENTS, WATCH_RESTARTS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Replace:
    """Full state of one resource type after a list; anything not in `items` is gone."""

    resource: str
    items: list[KubeObject]


@dataclass(frozen=True)
class Upsert:
    resource: str
    object: KubeObject


@dataclass(frozen=True)
class Delete:
    resource: str
    object: KubeObject


Change = Replace | Upsert | Delete


class Backoff:
    def __init__(self, initial: float = 1.0, maximum: float = 30.0) -> None:
        self._initial = initial
        self._maximum = maximum
        self._current = initial

    def next(self) -> float:
        delay = self._current
        self._current = min(self._current * 2, self._maximum)
        return delay

    def reset(self) -> None:
        self._current = self._initial


class ResourceWatcher:
    """List-then-watch loop for one resource type.

    The loop keeps the last seen resourceVersion and resumes from it after a dropped
    connection or server-side timeout, so no events are missed and none are replayed.
    Only when the server answers 410 Gone (the version was compacted away) does it fall
    back to a fresh list, which is emitted as a Replace so consumers can drop stale objects.
    """

    def __init__(
        self,
        reader: ClusterReader,
        resource: str,
        sink: Callable[[Change], None],
        stop: threading.Event,
        timeout_seconds: int = 300,
        backoff: Backoff | None = None,
        idle_delay: float = 1.0,
    ) -> None:
        self._idle_delay = idle_delay
        self._reader = reader
        self._resource = resource
        self._sink = sink
        self._stop = stop
        self._timeout_seconds = timeout_seconds
        self._backoff = backoff or Backoff()
        self.resource_version: str | None = None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.resource_version is None:
                    self._relist()
                received = self._watch_once()
                self._backoff.reset()
                WATCH_RESTARTS.labels(self._resource, "timeout").inc()
                if not received:
                    # A stream that closes without sending anything must not become a hot loop.
                    self._stop.wait(self._idle_delay)
            except ResourceVersionExpired:
                logger.info("%s: resourceVersion expired, relisting", self._resource)
                WATCH_RESTARTS.labels(self._resource, "expired").inc()
                self.resource_version = None
            except Exception:
                logger.exception("%s: watch failed, will resume", self._resource)
                WATCH_RESTARTS.labels(self._resource, "error").inc()
                self._stop.wait(self._backoff.next())

    def _relist(self) -> None:
        result = self._reader.list(self._resource)
        self._sink(Replace(self._resource, result.items))
        self.resource_version = result.resource_version

    def _watch_once(self) -> int:
        assert self.resource_version is not None
        received = 0
        stream = self._reader.watch(self._resource, self.resource_version, self._timeout_seconds)
        for event in stream:
            if self._stop.is_set():
                break
            received += 1
            WATCH_EVENTS.labels(self._resource, event.type).inc()
            if event.type == "ERROR":
                if event.object.get("code") == 410:
                    raise ResourceVersionExpired(event.object.get("message", ""))
                raise RuntimeError(f"watch error: {event.object.get('message')}")

            version = event.object.get("metadata", {}).get("resourceVersion")
            if event.type == "DELETED":
                self._sink(Delete(self._resource, event.object))
            elif event.type in ("ADDED", "MODIFIED"):
                self._sink(Upsert(self._resource, event.object))
            # BOOKMARK carries no object change, only a newer version to resume from.
            if version:
                self.resource_version = version
        return received

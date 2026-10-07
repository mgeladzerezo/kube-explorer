import asyncio
import threading

from app.kube.reader import ClusterReader
from app.watch.view import WATCHED_RESOURCES, ClusterView
from app.watch.watcher import Change, ResourceWatcher


class WatchManager:
    """Runs one watcher thread per resource and hands their changes to the event loop.

    The Kubernetes client is blocking, so each watch lives in its own thread. Changes are
    applied to the ClusterView with call_soon_threadsafe, which keeps the view and the hub
    single-threaded and lock-free.
    """

    def __init__(self, reader: ClusterReader, view: ClusterView, timeout_seconds: int) -> None:
        self._reader = reader
        self._view = view
        self._timeout_seconds = timeout_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        def sink(change: Change) -> None:
            loop.call_soon_threadsafe(self._view.apply, change)

        for resource in WATCHED_RESOURCES:
            watcher = ResourceWatcher(
                self._reader, resource, sink, self._stop, self._timeout_seconds
            )
            thread = threading.Thread(target=watcher.run, name=f"watch-{resource}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        # Threads blocked in a network read exit with the process; they are daemons.
        self._stop.set()

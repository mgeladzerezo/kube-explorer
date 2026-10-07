import threading

from app.kube.reader import ListResult, ResourceVersionExpired, WatchEvent
from app.watch.watcher import Backoff, Delete, Replace, ResourceWatcher, Upsert


def pod(name: str, version: str) -> dict:
    return {"metadata": {"name": name, "namespace": "shop", "resourceVersion": version}}


def event(kind: str, name: str, version: str) -> WatchEvent:
    return WatchEvent(kind, pod(name, version))


def bookmark(version: str) -> WatchEvent:
    return WatchEvent("BOOKMARK", {"metadata": {"resourceVersion": version}})


class ScriptedReader:
    """Replays list results and watch streams in order, then stops the watcher.

    A stream is a list of WatchEvents; an Exception in the list is raised at that point,
    which is how a dropped connection or a 410 looks to the caller.
    """

    def __init__(self, lists: list, streams: list[list], stop: threading.Event) -> None:
        self._lists = list(lists)
        self._streams = list(streams)
        self._stop = stop
        self.list_calls = 0
        self.watch_versions: list[str] = []

    def list(self, resource, namespace=None):
        self.list_calls += 1
        result = self._lists.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def watch(self, resource, resource_version, timeout_seconds):
        self.watch_versions.append(resource_version)
        if not self._streams:
            self._stop.set()
            return
        for item in self._streams.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


class RecordingBackoff(Backoff):
    def __init__(self) -> None:
        super().__init__(initial=0, maximum=0)
        self.delays = 0
        self.resets = 0

    def next(self) -> float:
        self.delays += 1
        return 0

    def reset(self) -> None:
        self.resets += 1


def run(lists, streams):
    stop = threading.Event()
    reader = ScriptedReader(lists, streams, stop)
    changes = []
    backoff = RecordingBackoff()
    watcher = ResourceWatcher(reader, "pods", changes.append, stop, backoff=backoff, idle_delay=0)
    watcher.run()
    return reader, changes, backoff, watcher


def test_lists_first_then_watches_from_the_list_version():
    reader, changes, _, _ = run([ListResult([pod("a", "5")], "100")], [[]])

    assert changes == [Replace("pods", [pod("a", "5")])]
    assert reader.watch_versions[0] == "100"


def test_events_become_changes_in_order():
    stream = [
        event("ADDED", "a", "101"),
        event("MODIFIED", "a", "102"),
        event("DELETED", "a", "103"),
    ]
    _, changes, _, _ = run([ListResult([], "100")], [stream])

    assert changes[1:] == [
        Upsert("pods", pod("a", "101")),
        Upsert("pods", pod("a", "102")),
        Delete("pods", pod("a", "103")),
    ]


def test_server_timeout_resumes_from_last_event_without_relisting():
    streams = [[event("ADDED", "a", "101"), event("MODIFIED", "a", "107")], []]
    reader, changes, _, _ = run([ListResult([], "100")], streams)

    assert reader.list_calls == 1
    assert reader.watch_versions == ["100", "107", "107"]
    assert sum(isinstance(c, Replace) for c in changes) == 1


def test_bookmark_advances_the_version_without_emitting_a_change():
    streams = [[event("ADDED", "a", "101"), bookmark("250")], []]
    reader, changes, _, _ = run([ListResult([], "100")], streams)

    assert reader.watch_versions[1] == "250"
    assert changes[1:] == [Upsert("pods", pod("a", "101"))]


def test_dropped_connection_resumes_from_last_seen_version_after_backoff():
    streams = [
        [event("ADDED", "a", "101"), ConnectionError("connection reset by peer")],
        [event("MODIFIED", "a", "102")],
    ]
    reader, changes, backoff, _ = run([ListResult([], "100")], streams)

    assert reader.list_calls == 1, "a dropped connection must not trigger a relist"
    assert reader.watch_versions[:2] == ["100", "101"]
    assert backoff.delays == 1
    assert changes[1:] == [Upsert("pods", pod("a", "101")), Upsert("pods", pod("a", "102"))]


def test_expired_version_triggers_relist_and_replace():
    lists = [
        ListResult([pod("a", "5"), pod("b", "6")], "100"),
        ListResult([pod("b", "900")], "950"),
    ]
    streams = [[event("MODIFIED", "a", "101"), ResourceVersionExpired("too old")], []]
    reader, changes, backoff, _ = run(lists, streams)

    assert reader.list_calls == 2
    assert reader.watch_versions[:2] == ["100", "950"]
    assert changes[-1] == Replace("pods", [pod("b", "900")])
    assert backoff.delays == 0, "410 is recovered by relisting immediately"


def test_410_delivered_as_error_event_is_treated_as_expired():
    gone = WatchEvent(
        "ERROR", {"kind": "Status", "code": 410, "message": "too old resource version"}
    )
    lists = [ListResult([], "100"), ListResult([pod("a", "300")], "300")]
    reader, changes, _, _ = run(lists, [[gone], []])

    assert reader.list_calls == 2
    assert reader.watch_versions[:2] == ["100", "300"]
    assert changes[-1] == Replace("pods", [pod("a", "300")])


def test_other_error_events_back_off_and_keep_the_version():
    failure = WatchEvent("ERROR", {"kind": "Status", "code": 500, "message": "etcd leader changed"})
    streams = [[event("ADDED", "a", "101"), failure], []]
    reader, _, backoff, _ = run([ListResult([], "100")], streams)

    assert reader.list_calls == 1
    assert reader.watch_versions[1] == "101"
    assert backoff.delays == 1


def test_failed_list_is_retried_with_backoff():
    lists = [ConnectionError("apiserver unavailable"), ListResult([pod("a", "5")], "100")]
    reader, changes, backoff, _ = run(lists, [[]])

    assert reader.list_calls == 2
    assert backoff.delays == 1
    assert changes == [Replace("pods", [pod("a", "5")])]


def test_backoff_resets_after_a_healthy_stream():
    streams = [[ConnectionError("reset")], [event("ADDED", "a", "101")], []]
    _, _, backoff, _ = run([ListResult([], "100")], streams)

    assert backoff.delays == 1
    assert backoff.resets >= 1


def test_stop_ends_the_loop_mid_stream():
    stop = threading.Event()
    changes = []

    def sink(change):
        changes.append(change)
        if isinstance(change, Upsert):
            stop.set()

    stream = [event("ADDED", "a", "101"), event("ADDED", "b", "102"), event("ADDED", "c", "103")]
    reader = ScriptedReader([ListResult([], "100")], [stream], stop)
    ResourceWatcher(reader, "pods", sink, stop, idle_delay=0).run()

    assert changes[1:] == [Upsert("pods", pod("a", "101"))]


def test_backoff_doubles_up_to_the_maximum_and_resets():
    backoff = Backoff(initial=1, maximum=5)
    assert [backoff.next() for _ in range(4)] == [1, 2, 4, 5]
    backoff.reset()
    assert backoff.next() == 1

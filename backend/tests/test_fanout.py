import asyncio

from app.watch.hub import Hub
from app.watch.view import ClusterView
from app.watch.watcher import Delete, Replace, Upsert


def pod(name: str, ready: bool = True, version: str = "1") -> dict:
    waiting = {} if ready else {"waiting": {"reason": "CrashLoopBackOff"}}
    return {
        "metadata": {"name": name, "namespace": "shop", "resourceVersion": version},
        "spec": {"containers": [{"name": "app"}], "nodeName": "worker-1"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": ready,
                    "restartCount": 0,
                    "state": waiting or {"running": {}},
                }
            ],
        },
    }


def deployment(name: str, desired: int, ready: int) -> dict:
    return {
        "metadata": {"name": name, "namespace": "shop"},
        "spec": {"replicas": desired},
        "status": {"readyReplicas": ready},
    }


def make_view() -> tuple[ClusterView, list]:
    published = []
    return ClusterView(published.append), published


def test_replace_publishes_upserts_and_removes_objects_missing_from_the_new_list():
    view, published = make_view()
    view.apply(Replace("pods", [pod("a"), pod("b")]))
    published.clear()

    # After a relist "a" is gone and "b" is unchanged: one delete, nothing else.
    view.apply(Replace("pods", [pod("b", version="77")]))

    assert published == [{"type": "delete", "collection": "pods", "id": "shop/a"}]
    assert [p["id"] for p in view.collection("pods")] == ["shop/b"]


def test_upsert_is_published_only_when_the_summary_changes():
    view, published = make_view()
    view.apply(Upsert("pods", pod("a")))
    view.apply(Upsert("pods", pod("a", version="2")))
    assert len(published) == 1

    view.apply(Upsert("pods", pod("a", ready=False, version="3")))
    assert published[-1]["item"]["status"] == "CrashLoopBackOff"
    assert published[-1]["item"]["failing"] is True


def test_delete_is_published_once():
    view, published = make_view()
    view.apply(Upsert("pods", pod("a")))
    view.apply(Delete("pods", pod("a")))
    view.apply(Delete("pods", pod("a")))

    assert [m["type"] for m in published] == ["upsert", "delete"]
    assert view.collection("pods") == []


def test_workload_kinds_share_one_collection_without_clobbering_each_other():
    view, _ = make_view()
    view.apply(Replace("deployments", [deployment("api", 3, 1)]))
    view.apply(Replace("statefulsets", [deployment("db", 1, 1)]))
    view.apply(Replace("deployments", [deployment("api", 3, 3)]))

    workloads = {w["id"]: w for w in view.snapshot()["collections"]["workloads"]}
    assert set(workloads) == {"Deployment/shop/api", "StatefulSet/shop/db"}
    assert workloads["Deployment/shop/api"]["failing"] is False


def test_event_messages_are_redacted_before_they_reach_browsers():
    view, published = make_view()
    view.apply(
        Upsert(
            "events",
            {
                "metadata": {"name": "api.1", "namespace": "shop"},
                "involvedObject": {"kind": "Pod", "name": "api"},
                "type": "Warning",
                "reason": "Failed",
                "message": "connect postgres://app:hunter2@db:5432/app failed",
            },
        )
    )
    assert "hunter2" not in published[0]["item"]["message"]
    assert "db:5432" in published[0]["item"]["message"]


def test_hub_delivers_every_message_to_every_subscriber_in_order():
    async def scenario():
        hub = Hub()
        first, second = hub.subscribe(), hub.subscribe()
        for n in range(3):
            hub.publish({"n": n})
        return [[(await s.next())["n"] for _ in range(3)] for s in (first, second)]

    assert asyncio.run(scenario()) == [[0, 1, 2], [0, 1, 2]]


def test_slow_subscriber_is_dropped_without_affecting_others():
    async def scenario():
        hub = Hub(max_queue=2)
        slow, fast = hub.subscribe(), hub.subscribe()
        received = []
        for n in range(5):
            hub.publish({"n": n})
            received.append((await fast.next())["n"])
        return hub, slow, received

    hub, slow, received = asyncio.run(scenario())
    assert received == [0, 1, 2, 3, 4]
    assert slow.dropped is True
    assert hub.subscriber_count == 1
    # The dropped subscriber is told to close instead of receiving a stream with a hole in it.
    assert asyncio.run(slow.next()) is None


def test_subscribing_after_a_change_sees_it_in_the_snapshot_not_the_stream():
    async def scenario():
        hub = Hub()
        view = ClusterView(hub.publish)
        view.apply(Upsert("pods", pod("a")))
        subscription = hub.subscribe()
        snapshot = view.snapshot()
        view.apply(Upsert("pods", pod("b")))
        return snapshot, await subscription.next()

    snapshot, message = asyncio.run(scenario())
    assert [p["name"] for p in snapshot["collections"]["pods"]] == ["a"]
    assert message["item"]["name"] == "b"


def test_unsubscribed_clients_receive_nothing_further():
    hub = Hub()
    subscription = hub.subscribe()
    hub.unsubscribe(subscription)
    hub.publish({"n": 1})
    assert hub.subscriber_count == 0

from collections.abc import Callable
from typing import Any

from app.diagnosis.redact import redact_text
from app.kube.reader import KINDS, KubeObject
from app.kube.status import pod_health, workload_health
from app.watch.hub import Message
from app.watch.watcher import Change, Delete, Replace

WATCHED_RESOURCES = ("pods", "deployments", "statefulsets", "daemonsets", "events")
MAX_EVENTS = 500

# Watched resource -> collection name in the browser protocol.
_COLLECTIONS = {
    "pods": "pods",
    "deployments": "workloads",
    "statefulsets": "workloads",
    "daemonsets": "workloads",
    "events": "events",
}


def _meta(obj: KubeObject) -> dict[str, Any]:
    return obj.get("metadata", {})


def summarize_pod(pod: KubeObject) -> dict[str, Any]:
    meta = _meta(pod)
    health = pod_health(pod)
    owners = meta.get("ownerReferences", [])
    return {
        "id": f"{meta.get('namespace')}/{meta['name']}",
        "namespace": meta.get("namespace"),
        "name": meta["name"],
        "status": health.reason,
        "failing": health.failing,
        "ready": health.ready,
        "total": health.total,
        "restarts": health.restarts,
        "node": pod.get("spec", {}).get("nodeName"),
        "owner": f"{owners[0]['kind']}/{owners[0]['name']}" if owners else None,
        "containers": [c["name"] for c in pod.get("spec", {}).get("containers", [])],
        "createdAt": meta.get("creationTimestamp"),
    }


def summarize_workload(kind: str, workload: KubeObject) -> dict[str, Any]:
    meta = _meta(workload)
    health = workload_health(kind, workload)
    return {
        "id": f"{kind}/{meta.get('namespace')}/{meta['name']}",
        "kind": kind,
        "namespace": meta.get("namespace"),
        "name": meta["name"],
        "desired": health.desired,
        "ready": health.ready,
        "failing": health.failing,
        "createdAt": meta.get("creationTimestamp"),
    }


def summarize_event(event: KubeObject) -> dict[str, Any]:
    meta = _meta(event)
    involved = event.get("involvedObject", {})
    return {
        "id": f"{meta.get('namespace')}/{meta['name']}",
        "namespace": meta.get("namespace"),
        "type": event.get("type", "Normal"),
        "reason": event.get("reason", ""),
        "object": f"{involved.get('kind')}/{involved.get('name')}",
        "message": redact_text(event.get("message", "")),
        "count": event.get("count") or 1,
        "lastSeen": event.get("lastTimestamp") or event.get("eventTime"),
    }


def _summarize(resource: str, obj: KubeObject) -> dict[str, Any]:
    if resource == "pods":
        return summarize_pod(obj)
    if resource == "events":
        return summarize_event(obj)
    return summarize_workload(KINDS[resource], obj)


class ClusterView:
    """Current summaries of the watched resources, kept in step with the watch streams.

    Every change is turned into `upsert` / `delete` messages for the browsers. All methods
    run on the event loop thread, which is what makes "subscribe, then snapshot" gap-free.
    """

    def __init__(self, publish: Callable[[Message], None]) -> None:
        self._publish = publish
        self._items: dict[str, dict[str, dict[str, Any]]] = {r: {} for r in WATCHED_RESOURCES}

    def apply(self, change: Change) -> None:
        items = self._items[change.resource]
        collection = _COLLECTIONS[change.resource]

        if isinstance(change, Replace):
            fresh = {s["id"]: s for s in (_summarize(change.resource, o) for o in change.items)}
            for stale_id in items.keys() - fresh.keys():
                self._publish({"type": "delete", "collection": collection, "id": stale_id})
            for item_id, summary in fresh.items():
                if items.get(item_id) != summary:
                    self._publish({"type": "upsert", "collection": collection, "item": summary})
            self._items[change.resource] = fresh
            return

        summary = _summarize(change.resource, change.object)
        if isinstance(change, Delete):
            if items.pop(summary["id"], None) is not None:
                self._publish({"type": "delete", "collection": collection, "id": summary["id"]})
        elif items.get(summary["id"]) != summary:
            # Most MODIFIED events (status heartbeats, managedFields) do not change the summary.
            items[summary["id"]] = summary
            self._publish({"type": "upsert", "collection": collection, "item": summary})

    def collection(self, name: str) -> list[dict[str, Any]]:
        items = [
            item
            for resource, collection in _COLLECTIONS.items()
            if collection == name
            for item in self._items[resource].values()
        ]
        if name == "events":
            items.sort(key=lambda e: e["lastSeen"] or "", reverse=True)
            return items[:MAX_EVENTS]
        return sorted(items, key=lambda i: i["id"])

    def snapshot(self) -> Message:
        return {
            "type": "snapshot",
            "collections": {
                name: self.collection(name) for name in ("pods", "workloads", "events")
            },
        }

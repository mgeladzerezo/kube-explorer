from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

KubeObject = dict[str, Any]

# Plural resource name -> Kind. Every resource the app reads is listed here, and the
# Helm ClusterRole grants get/list/watch on exactly this set. Secrets are deliberately absent.
KINDS: dict[str, str] = {
    "pods": "Pod",
    "deployments": "Deployment",
    "replicasets": "ReplicaSet",
    "statefulsets": "StatefulSet",
    "daemonsets": "DaemonSet",
    "services": "Service",
    "endpoints": "Endpoints",
    "events": "Event",
    "persistentvolumeclaims": "PersistentVolumeClaim",
    "configmaps": "ConfigMap",
    "networkpolicies": "NetworkPolicy",
    "resourcequotas": "ResourceQuota",
    "nodes": "Node",
}
PLURALS: dict[str, str] = {kind: plural for plural, kind in KINDS.items()}


class ResourceVersionExpired(Exception):
    """The API server no longer has the requested resourceVersion (HTTP 410)."""


class LogUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ListResult:
    items: list[KubeObject]
    resource_version: str


@dataclass(frozen=True)
class WatchEvent:
    type: str  # ADDED | MODIFIED | DELETED | BOOKMARK | ERROR
    object: KubeObject


class ClusterReader(Protocol):
    """Read-only view of a cluster. Objects are plain dicts in the API's JSON shape."""

    def list(self, resource: str, namespace: str | None = None) -> ListResult: ...

    def watch(
        self, resource: str, resource_version: str, timeout_seconds: int
    ) -> Iterator[WatchEvent]: ...

    def read_log(
        self, namespace: str, pod: str, container: str, tail_lines: int, previous: bool = False
    ) -> str: ...


def object_key(obj: KubeObject) -> str:
    meta = obj.get("metadata", {})
    namespace = meta.get("namespace")
    return f"{namespace}/{meta['name']}" if namespace else meta["name"]

import time
from collections.abc import Iterator
from pathlib import Path

import yaml

from app.kube.reader import (
    KINDS,
    PLURALS,
    KubeObject,
    ListResult,
    LogUnavailable,
    WatchEvent,
    object_key,
)

LogKey = tuple[str, str, str, bool]  # namespace, pod, container, previous


class FakeCluster:
    """ClusterReader over recorded objects. Used by tests, the eval harness and fixture mode."""

    def __init__(
        self,
        objects: dict[str, list[KubeObject]] | None = None,
        logs: dict[LogKey, str] | None = None,
    ) -> None:
        self.objects: dict[str, list[KubeObject]] = {resource: [] for resource in KINDS}
        for resource, items in (objects or {}).items():
            self.objects[resource] = list(items)
        self.logs: dict[LogKey, str] = dict(logs or {})

    def list(self, resource: str, namespace: str | None = None) -> ListResult:
        items = self.objects[resource]
        if namespace:
            items = [o for o in items if o.get("metadata", {}).get("namespace") == namespace]
        return ListResult(items=list(items), resource_version="1")

    def watch(
        self, resource: str, resource_version: str, timeout_seconds: int
    ) -> Iterator[WatchEvent]:
        # Recorded state never changes; behave like a quiet watch that times out.
        time.sleep(min(timeout_seconds, 1))
        return iter(())

    def read_log(
        self, namespace: str, pod: str, container: str, tail_lines: int, previous: bool = False
    ) -> str:
        try:
            text = self.logs[(namespace, pod, container, previous)]
        except KeyError as exc:
            raise LogUnavailable(f"no recorded log for {pod}/{container}") from exc
        return "\n".join(text.splitlines()[-tail_lines:])

    def merge(self, other: "FakeCluster") -> None:
        for resource, items in other.objects.items():
            known = {object_key(o) for o in self.objects[resource]}
            self.objects[resource].extend(o for o in items if object_key(o) not in known)
        self.logs.update(other.logs)


def load_capture(directory: Path) -> FakeCluster:
    """Load one capture: cluster.yaml plus logs/<pod>.<container>[.previous].log."""
    cluster = FakeCluster()
    with (directory / "cluster.yaml").open(encoding="utf-8") as handle:
        for document in yaml.safe_load_all(handle):
            if document:
                cluster.objects[PLURALS[document["kind"]]].append(document)

    namespaces = {
        pod["metadata"]["name"]: pod["metadata"]["namespace"] for pod in cluster.objects["pods"]
    }
    for log_file in sorted((directory / "logs").glob("*.log")):
        stem = log_file.name.removesuffix(".log")
        previous = stem.endswith(".previous")
        pod, container = stem.removesuffix(".previous").rsplit(".", 1)
        key = (namespaces[pod], pod, container, previous)
        cluster.logs[key] = log_file.read_text(encoding="utf-8")
    return cluster


def load_capture_tree(root: Path) -> FakeCluster:
    """Merge every <root>/<scenario>/captured directory into one cluster."""
    merged = FakeCluster()
    for capture in sorted(root.glob("*/captured")):
        merged.merge(load_capture(capture))
    return merged

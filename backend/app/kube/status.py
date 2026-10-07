"""Derives the one-word status and "is this failing" flag shown in the UI from raw objects."""

from dataclasses import dataclass

from app.kube.reader import KubeObject

_HEALTHY_REASONS = {"Running", "Completed", "Succeeded", "ContainerCreating", "PodInitializing"}


@dataclass(frozen=True)
class PodHealth:
    reason: str
    failing: bool
    ready: int
    total: int
    restarts: int


def _container_reason(status: KubeObject) -> str | None:
    state = status.get("state", {})
    if "waiting" in state:
        return state["waiting"].get("reason")
    if "terminated" in state:
        terminated = state["terminated"]
        return terminated.get("reason") or f"ExitCode:{terminated.get('exitCode')}"
    return None


def _init_reason(statuses: list[KubeObject]) -> str | None:
    for status in statuses:
        state = status.get("state", {})
        terminated = state.get("terminated")
        if terminated and terminated.get("exitCode") == 0:
            continue
        reason = _container_reason(status)
        if reason and reason != "PodInitializing":
            return f"Init:{reason}"
        if not terminated:
            return None
    return None


def is_unschedulable(pod: KubeObject) -> bool:
    return any(
        c.get("type") == "PodScheduled" and c.get("status") == "False"
        for c in pod.get("status", {}).get("conditions", [])
    )


def pod_health(pod: KubeObject) -> PodHealth:
    """Mirrors the STATUS column of `kubectl get pods`."""
    status = pod.get("status", {})
    containers = status.get("containerStatuses", [])
    init_containers = status.get("initContainerStatuses", [])

    reason = status.get("reason") or status.get("phase", "Unknown")
    init_reason = _init_reason(init_containers)
    if init_reason:
        reason = init_reason
    else:
        for container in containers:
            container_reason = _container_reason(container)
            if container_reason:
                reason = container_reason
                break
    if pod.get("metadata", {}).get("deletionTimestamp"):
        reason = "Terminating"
    elif is_unschedulable(pod):
        reason = "Unschedulable"

    ready = sum(1 for c in containers if c.get("ready"))
    total = len(pod.get("spec", {}).get("containers", []))
    restarts = sum(c.get("restartCount", 0) for c in containers + init_containers)

    if reason == "Terminating":
        failing = False
    elif reason == "Running":
        failing = ready < total
    elif reason == "Pending":
        failing = False
    else:
        failing = reason not in _HEALTHY_REASONS
    return PodHealth(reason=reason, failing=failing, ready=ready, total=total, restarts=restarts)


@dataclass(frozen=True)
class WorkloadHealth:
    desired: int
    ready: int
    failing: bool


def workload_health(kind: str, workload: KubeObject) -> WorkloadHealth:
    status = workload.get("status", {})
    if kind == "DaemonSet":
        desired = status.get("desiredNumberScheduled", 0)
        ready = status.get("numberReady", 0)
    else:
        desired = workload.get("spec", {}).get("replicas", 1)
        ready = status.get("readyReplicas", 0)
    return WorkloadHealth(desired=desired, ready=ready, failing=ready < desired)

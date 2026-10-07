"""Builds faults/<id>/captured/ for every scenario without needing a cluster.

Each capture is the scenario's own inject.yaml plus a model of how a three-node kind
cluster reacts to it: the pods the controllers create, their container statuses, the
events the scheduler and kubelet emit, and the container logs. The reactions below were
written from the behaviour these faults are known to produce; they are not recordings.
`faults/capture.sh` replaces a capture with real cluster output.

    python faults/tools/synthesize_captures.py
"""

import copy
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FAULTS_DIR = Path(__file__).resolve().parents[1]

CREATED = "2026-09-28T09:14:02Z"
STARTED = "2026-09-28T09:14:09Z"
LAST_SEEN = "2026-09-28T09:21:40Z"
WORKER = "kube-explorer-worker"
SCHEDULER_PREEMPTION = "preemption: 0/3 nodes are available: 3 Preemption is not helpful for scheduling."
TAINTS = (
    "1 node(s) had untolerated taint {dedicated: batch}, "
    "1 node(s) had untolerated taint {node-role.kubernetes.io/control-plane: }"
)

Obj = dict[str, Any]
EventSpec = tuple[str, str, str, int]  # type, reason, message, count


def running(ready: bool = True, restarts: int = 0, last: Obj | None = None) -> Obj:
    return {
        "state": {"running": {"startedAt": STARTED}},
        "lastState": last or {},
        "ready": ready,
        "started": True,
        "restartCount": restarts,
    }


def waiting(reason: str, message: str = "", restarts: int = 0, last: Obj | None = None) -> Obj:
    state = {"reason": reason, **({"message": message} if message else {})}
    return {
        "state": {"waiting": state},
        "lastState": last or {},
        "ready": False,
        "started": False,
        "restartCount": restarts,
    }


def exited(reason: str, code: int, message: str = "") -> Obj:
    terminated = {
        "reason": reason,
        "exitCode": code,
        "startedAt": "2026-09-28T09:18:51Z",
        "finishedAt": "2026-09-28T09:18:53Z",
    }
    if message:
        terminated["message"] = message
    return {"terminated": terminated}


@dataclass
class Reaction:
    """How one workload's pods end up after the fault is injected."""

    phase: str = "Running"
    unschedulable: str | None = None
    container: Obj = field(default_factory=running)
    init: Obj | None = None
    pod_events: list[EventSpec] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)
    previous_logs: dict[str, str] = field(default_factory=dict)
    created_replicas: int | None = None
    object_events: dict[tuple[str, str], list[EventSpec]] = field(default_factory=dict)
    replica_failure: str | None = None


NGINX_LOG = """\
/docker-entrypoint.sh: Configuration complete; ready for start up
2026/09/28 09:14:09 [notice] 1#1: using the "epoll" event method
2026/09/28 09:14:09 [notice] 1#1: nginx/1.27.2
2026/09/28 09:14:09 [notice] 1#1: start worker processes
"""

CRASH_BACKOFF = "back-off 2m40s restarting failed container"
START_ERROR = (
    "failed to create containerd task: failed to create shim task: OCI runtime create failed: "
    'runc create failed: unable to start container process: exec: "/usr/local/bin/notifier": '
    "stat /usr/local/bin/notifier: no such file or directory: unknown"
)
BAD_TAG_PULL = (
    'Failed to pull image "nginx:1.27.99-alpine": rpc error: code = NotFound desc = failed to '
    'pull and unpack image "docker.io/library/nginx:1.27.99-alpine": failed to resolve reference '
    '"docker.io/library/nginx:1.27.99-alpine": docker.io/library/nginx:1.27.99-alpine: not found'
)
PRIVATE_IMAGE = "ghcr.io/kube-explorer-faults/pricing-api:1.4.2"
PRIVATE_PULL = (
    f'Failed to pull image "{PRIVATE_IMAGE}": failed to pull and unpack image "{PRIVATE_IMAGE}": '
    f'failed to resolve reference "{PRIVATE_IMAGE}": failed to authorize: failed to fetch '
    "anonymous token: unexpected status from GET request to https://ghcr.io/token?"
    "scope=repository%3Akube-explorer-faults%2Fpricing-api%3Apull&service=ghcr.io: 403 Forbidden"
)
OOM_LOG = "loading report dataset into memory\n" + "".join(
    f"loaded {n} MiB\n" for n in range(8, 64, 8)
)
MIGRATE_LOG = """\
running migrations against postgres://inventory:Tr0ub4dor-inventory@inventory-db:5432/inventory
migrate: /migrations/0001_init.sql is missing from the image
"""
RBAC_LOG = (
    'Error from server (Forbidden): pods is forbidden: User "system:serviceaccount:'
    'fault-rbac-forbidden:pod-lister" cannot list resource "pods" in API group "" in the '
    'namespace "fault-rbac-forbidden"\n'
)
ROOTFS_LOG = """\
/docker-entrypoint.sh: /docker-entrypoint.d/ is not empty, will attempt to perform configuration
/docker-entrypoint.sh: Looking for shell scripts in /docker-entrypoint.d/
10-listen-on-ipv6-by-default.sh: info: can not modify /etc/nginx/conf.d/default.conf (read-only file system?)
/docker-entrypoint.sh: Configuration complete; ready for start up
2026/09/28 09:18:52 [emerg] 1#1: mkdir() "/var/cache/nginx/client_temp" failed (30: Read-only file system)
nginx: [emerg] mkdir() "/var/cache/nginx/client_temp" failed (30: Read-only file system)
"""
READINESS_LOG = NGINX_LOG + "".join(
    f'2026/09/28 09:21:{s:02d} [error] 30#30: *{n} open() "/usr/share/nginx/html/healthz" failed '
    f"(2: No such file or directory), client: 10.244.1.1, server: localhost, "
    f'request: "GET /healthz HTTP/1.1", host: "10.244.1.10:80"\n'
    f'10.244.1.1 - - [28/Sep/2026:09:21:{s:02d} +0000] "GET /healthz HTTP/1.1" 404 153 "-" '
    f'"kube-probe/1.31" "-"\n'
    for n, s in enumerate(range(20, 40, 5), start=80)
)
DNS_LOG = (
    "wget: bad address 'payments.fault-netpol-blocks-dns.svc.cluster.local'\n"
    "payments request failed\n"
) * 6
LIVENESS_PREVIOUS_LOG = NGINX_LOG + (
    "2026/09/28 09:18:52 [notice] 1#1: signal 3 (SIGQUIT) received, shutting down\n"
    "2026/09/28 09:18:52 [notice] 1#1: exit\n"
)


def crash_loop(container: str, last: Obj, restarts: int = 6) -> Obj:
    return waiting("CrashLoopBackOff", f"{CRASH_BACKOFF}={container}", restarts, last)


def back_off_event(container: str, count: int = 31) -> EventSpec:
    return ("Warning", "BackOff", f"Back-off restarting failed container {container}", count)


def unschedulable(reasons: str, preemption: str = SCHEDULER_PREEMPTION) -> Reaction:
    return Reaction(
        phase="Pending", unschedulable=f"0/3 nodes are available: {reasons}. {preemption}"
    )


# (scenario id, workload name) -> reaction. Workloads not listed stay healthy.
REACTIONS: dict[tuple[str, str], Reaction] = {
    ("oom-killed", "report-worker"): Reaction(
        container=crash_loop("worker", exited("OOMKilled", 137)),
        pod_events=[back_off_event("worker")],
        logs={"worker": OOM_LOG},
        previous_logs={"worker": OOM_LOG},
    ),
    ("readiness-probe-failing", "storefront"): Reaction(
        container=running(ready=False),
        pod_events=[
            ("Warning", "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 404", 84)
        ],
        logs={"storefront": READINESS_LOG},
    ),
    ("missing-configmap-key", "orders"): Reaction(
        phase="Pending",
        container=waiting(
            "CreateContainerConfigError",
            "couldn't find key APP_MODE in ConfigMap fault-missing-configmap-key/app-config",
        ),
        pod_events=[
            (
                "Warning",
                "Failed",
                "Error: couldn't find key APP_MODE in ConfigMap fault-missing-configmap-key/app-config",
                22,
            )
        ],
    ),
    ("netpol-blocks-dns", "checkout"): Reaction(
        container=running(ready=False),
        pod_events=[
            (
                "Warning",
                "Unhealthy",
                "Readiness probe failed: ;; connection timed out; no servers could be reached",
                40,
            )
        ],
        logs={"checkout": DNS_LOG},
    ),
    ("pvc-pending", "media"): Reaction(
        phase="Pending",
        unschedulable=(
            "0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims. "
            + SCHEDULER_PREEMPTION
        ),
        object_events={
            ("PersistentVolumeClaim", "uploads"): [
                ("Warning", "ProvisioningFailed", 'storageclass.storage.k8s.io "fast-ssd" not found', 30)
            ]
        },
    ),
    ("bad-image-tag", "catalog"): Reaction(
        phase="Pending",
        container=waiting("ImagePullBackOff", 'Back-off pulling image "nginx:1.27.99-alpine"'),
        pod_events=[
            ("Normal", "Pulling", 'Pulling image "nginx:1.27.99-alpine"', 4),
            ("Warning", "Failed", BAD_TAG_PULL, 4),
            ("Warning", "Failed", "Error: ErrImagePull", 4),
            ("Normal", "BackOff", 'Back-off pulling image "nginx:1.27.99-alpine"', 18),
            ("Warning", "Failed", "Error: ImagePullBackOff", 18),
        ],
    ),
    ("crashloop-bad-command", "notifier"): Reaction(
        container=crash_loop("notifier", exited("StartError", 128, START_ERROR), restarts=7),
        pod_events=[
            ("Warning", "Failed", f"Error: {START_ERROR}", 8),
            back_off_event("notifier"),
        ],
    ),
    ("quota-exceeded", "api"): Reaction(
        created_replicas=2,
        replica_failure=(
            'pods "api-{hash}-" is forbidden: exceeded quota: compute-quota, requested: '
            "requests.cpu=250m, used: requests.cpu=500m, limited: requests.cpu=500m"
        ),
    ),
    ("missing-secret", "billing"): Reaction(
        phase="Pending",
        container=waiting("CreateContainerConfigError", 'secret "billing-db-credentials" not found'),
        pod_events=[("Warning", "Failed", 'Error: secret "billing-db-credentials" not found', 26)],
    ),
    ("liveness-probe-failing", "gateway"): Reaction(
        container=crash_loop("gateway", exited("Completed", 0), restarts=9),
        pod_events=[
            (
                "Warning",
                "Unhealthy",
                'Liveness probe failed: Get "http://10.244.1.10:8080/": dial tcp 10.244.1.10:8080: '
                "connect: connection refused",
                30,
            ),
            ("Normal", "Killing", "Container gateway failed liveness probe, will be restarted", 10),
            back_off_event("gateway", 12),
        ],
        previous_logs={"gateway": LIVENESS_PREVIOUS_LOG},
    ),
    ("unschedulable-resources", "analytics"): unschedulable(
        f"1 Insufficient memory, {TAINTS}",
        "preemption: 0/3 nodes are available: 1 No preemption victims found for incoming pod, "
        "2 Preemption is not helpful for scheduling.",
    ),
    ("node-selector-mismatch", "search"): unschedulable(
        f"1 node(s) didn't match Pod's node affinity/selector, {TAINTS}"
    ),
    # Same scheduler message as node-selector-mismatch: one worker fails the selector and the
    # other two fail on taints. Only the pod's nodeSelector and the node labels tell them apart.
    ("untolerated-taint", "nightly-batch"): unschedulable(
        f"1 node(s) didn't match Pod's node affinity/selector, {TAINTS}"
    ),
    ("init-container-failing", "inventory"): Reaction(
        phase="Pending",
        init=crash_loop("migrate", exited("Error", 1), restarts=5),
        container=waiting("PodInitializing"),
        pod_events=[back_off_event("migrate", 24)],
        logs={"migrate": MIGRATE_LOG},
        previous_logs={"migrate": MIGRATE_LOG},
    ),
    ("missing-configmap", "edge-proxy"): Reaction(
        phase="Pending",
        container=waiting("ContainerCreating"),
        pod_events=[
            (
                "Warning",
                "FailedMount",
                'MountVolume.SetUp failed for volume "config" : configmap "edge-proxy-conf" not found',
                12,
            )
        ],
    ),
    ("rbac-forbidden", "pod-lister"): Reaction(
        container=crash_loop("lister", exited("Error", 1)),
        pod_events=[back_off_event("lister")],
        logs={"lister": RBAC_LOG},
        previous_logs={"lister": RBAC_LOG},
    ),
    ("read-only-rootfs", "assets"): Reaction(
        container=crash_loop("assets", exited("Error", 1)),
        pod_events=[back_off_event("assets")],
        logs={"assets": ROOTFS_LOG},
        previous_logs={"assets": ROOTFS_LOG},
    ),
    ("private-registry-no-pull-secret", "pricing"): Reaction(
        phase="Pending",
        container=waiting("ImagePullBackOff", f'Back-off pulling image "{PRIVATE_IMAGE}"'),
        pod_events=[
            ("Normal", "Pulling", f'Pulling image "{PRIVATE_IMAGE}"', 4),
            ("Warning", "Failed", PRIVATE_PULL, 4),
            ("Warning", "Failed", "Error: ErrImagePull", 4),
            ("Normal", "BackOff", f'Back-off pulling image "{PRIVATE_IMAGE}"', 18),
            ("Warning", "Failed", "Error: ImagePullBackOff", 18),
        ],
    ),
}

# Status the API server reports for non-workload objects in specific scenarios.
PVC_PHASE = {("pvc-pending", "uploads"): "Pending"}
QUOTA_USED = {
    ("quota-exceeded", "compute-quota"): {"requests.cpu": "500m", "requests.memory": "128Mi"}
}


def nodes() -> list[Obj]:
    def node(name: str, labels: Obj, taints: list[Obj]) -> Obj:
        return {
            "apiVersion": "v1",
            "kind": "Node",
            "metadata": {
                "name": name,
                "labels": {"kubernetes.io/hostname": name, "kubernetes.io/os": "linux", **labels},
            },
            "spec": {"taints": taints} if taints else {},
            "status": {
                "allocatable": {"cpu": "8", "memory": "16304520Ki", "pods": "110"},
                "conditions": [{"type": "Ready", "status": "True", "reason": "KubeletReady"}],
            },
        }

    control_plane = "node-role.kubernetes.io/control-plane"
    return [
        node(
            "kube-explorer-control-plane",
            {control_plane: ""},
            [{"key": control_plane, "effect": "NoSchedule"}],
        ),
        node(WORKER, {}, []),
        node(
            "kube-explorer-worker2",
            {"dedicated": "batch"},
            [{"key": "dedicated", "value": "batch", "effect": "NoSchedule"}],
        ),
    ]


def short_hash(text: str, length: int) -> str:
    alphabet = "bcdfghjklmnpqrstvwxz2456789"
    digest = hashlib.sha256(text.encode()).digest()
    return "".join(alphabet[b % len(alphabet)] for b in digest[:length])


class Capture:
    def __init__(self, scenario_id: str, manifests: list[Obj]) -> None:
        self.scenario_id = scenario_id
        self.objects: list[Obj] = []
        self.logs: dict[str, str] = {}
        self._event_count = 0
        self._pod_count = 0
        self._pods: list[Obj] = []

        for manifest in manifests:
            kind = manifest["kind"]
            if kind == "Deployment":
                self._add_deployment(manifest)
            elif kind == "PersistentVolumeClaim":
                phase = PVC_PHASE.get((scenario_id, manifest["metadata"]["name"]), "Bound")
                self.objects.append({**manifest, "status": {"phase": phase}})
            elif kind == "ResourceQuota":
                used = QUOTA_USED.get((scenario_id, manifest["metadata"]["name"]), {})
                status = {"hard": manifest["spec"]["hard"], "used": used}
                self.objects.append({**manifest, "status": status})
            elif kind in ("ConfigMap", "NetworkPolicy", "Service"):
                self.objects.append(manifest)
        for service in [o for o in self.objects if o["kind"] == "Service"]:
            self.objects.append(self._endpoints(service))
        self.objects.extend(nodes())

    def _event(self, kind: str, name: str, namespace: str, spec: EventSpec) -> None:
        event_type, reason, message, count = spec
        self._event_count += 1
        self.objects.append(
            {
                "apiVersion": "v1",
                "kind": "Event",
                "metadata": {
                    "name": f"{name}.{short_hash(f'{name}{self._event_count}', 12)}",
                    "namespace": namespace,
                },
                "involvedObject": {"kind": kind, "name": name, "namespace": namespace},
                "type": event_type,
                "reason": reason,
                "message": message,
                "count": count,
                "firstTimestamp": CREATED,
                "lastTimestamp": LAST_SEEN if count > 1 else STARTED,
            }
        )

    def _add_deployment(self, deployment: Obj) -> None:
        name = deployment["metadata"]["name"]
        namespace = deployment["metadata"]["namespace"]
        reaction = REACTIONS.get((self.scenario_id, name), Reaction())
        desired = deployment["spec"]["replicas"]
        created = reaction.created_replicas if reaction.created_replicas is not None else desired
        rs_hash = short_hash(f"{namespace}/{name}", 9)
        rs_name = f"{name}-{rs_hash}"

        pods = [
            self._pod(deployment, rs_name, f"{rs_name}-{short_hash(f'{rs_name}{i}', 5)}", reaction)
            for i in range(created)
        ]
        ready = sum(1 for p in pods if all(c["ready"] for c in p["status"]["containerStatuses"]))

        conditions = [
            {
                "type": "Available",
                "status": "True" if ready >= desired else "False",
                "reason": "MinimumReplicasAvailable"
                if ready >= desired
                else "MinimumReplicasUnavailable",
            },
            {"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
        ]
        failure = reaction.replica_failure and reaction.replica_failure.format(hash=rs_hash)
        if failure:
            conditions.append(
                {"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate", "message": failure}
            )
        status = {
            "replicas": created,
            "updatedReplicas": created,
            "readyReplicas": ready,
            "unavailableReplicas": desired - ready,
            "conditions": conditions,
        }
        self.objects.append(
            {**deployment, "metadata": {**deployment["metadata"], "creationTimestamp": CREATED},
             "status": status}
        )
        self.objects.append(
            {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {
                    "name": rs_name,
                    "namespace": namespace,
                    "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": name}],
                },
                "spec": {"replicas": desired},
                "status": {"replicas": created, "readyReplicas": ready},
            }
        )
        self.objects.extend(pods)
        self._event(
            "Deployment", name, namespace,
            ("Normal", "ScalingReplicaSet", f"Scaled up replica set {rs_name} to {desired}", 1),
        )
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            self._event(
                "ReplicaSet", rs_name, namespace,
                ("Normal", "SuccessfulCreate", f"Created pod: {pod_name}", 1),
            )
        if failure:
            self._event(
                "ReplicaSet", rs_name, namespace, ("Warning", "FailedCreate", f"Error creating: {failure}", 14)
            )
        for (kind, object_name), specs in reaction.object_events.items():
            for spec in specs:
                self._event(kind, object_name, namespace, spec)

    def _pod(self, deployment: Obj, rs_name: str, pod_name: str, reaction: Reaction) -> Obj:
        namespace = deployment["metadata"]["namespace"]
        template = copy.deepcopy(deployment["spec"]["template"])
        spec = template["spec"]
        spec.setdefault("serviceAccountName", "default")
        scheduled = reaction.unschedulable is None
        self._pod_count += 1
        pod_ip = f"10.244.1.{9 + self._pod_count}"

        status: Obj = {"phase": reaction.phase}
        if not scheduled:
            status["conditions"] = [
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": reaction.unschedulable,
                }
            ]
            status["containerStatuses"] = []
            self._event(
                "Pod", pod_name, namespace, ("Warning", "FailedScheduling", reaction.unschedulable, 9)
            )
        else:
            spec["nodeName"] = WORKER
            status["podIP"] = pod_ip
            status["containerStatuses"] = [
                {"name": c["name"], "image": c["image"], **copy.deepcopy(reaction.container)}
                for c in spec["containers"]
            ]
            if "initContainers" in spec:
                status["initContainerStatuses"] = [
                    {"name": c["name"], "image": c["image"], **copy.deepcopy(reaction.init or {})}
                    for c in spec["initContainers"]
                ]
            ready = all(c["ready"] for c in status["containerStatuses"])
            status["conditions"] = [
                {"type": "PodScheduled", "status": "True"},
                {"type": "Initialized", "status": "False" if reaction.init else "True"},
                {"type": "ContainersReady", "status": "True" if ready else "False"},
                {"type": "Ready", "status": "True" if ready else "False"},
            ]
            self._event(
                "Pod", pod_name, namespace,
                ("Normal", "Scheduled", f"Successfully assigned {namespace}/{pod_name} to {WORKER}", 1),
            )
            for event in reaction.pod_events:
                self._event("Pod", pod_name, namespace, event)
            self._record_logs(pod_name, spec, status, reaction)

        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {**template["metadata"]["labels"], "pod-template-hash": rs_name.rsplit("-", 1)[1]},
                "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": rs_name}],
                "creationTimestamp": CREATED,
            },
            "spec": spec,
            "status": status,
        }

    def _record_logs(self, pod_name: str, spec: Obj, status: Obj, reaction: Reaction) -> None:
        for container, text in reaction.logs.items():
            self.logs[f"{pod_name}.{container}.log"] = text
        for container, text in reaction.previous_logs.items():
            self.logs[f"{pod_name}.{container}.previous.log"] = text
        for container in status["containerStatuses"]:
            healthy = "running" in container["state"] and container["name"] not in reaction.logs
            if healthy and container["image"].startswith("nginx"):
                self.logs[f"{pod_name}.{container['name']}.log"] = NGINX_LOG

    def _endpoints(self, service: Obj) -> Obj:
        selector = service["spec"]["selector"]
        namespace = service["metadata"]["namespace"]
        ready, not_ready = [], []
        for pod in (o for o in self.objects if o["kind"] == "Pod"):
            labels = pod["metadata"]["labels"]
            if pod["metadata"]["namespace"] != namespace or "podIP" not in pod["status"]:
                continue
            if any(labels.get(k) != v for k, v in selector.items()):
                continue
            address = {"ip": pod["status"]["podIP"], "targetRef": {"kind": "Pod", "name": pod["metadata"]["name"]}}
            is_ready = all(c["ready"] for c in pod["status"]["containerStatuses"])
            (ready if is_ready else not_ready).append(address)

        endpoints: Obj = {
            "apiVersion": "v1",
            "kind": "Endpoints",
            "metadata": {"name": service["metadata"]["name"], "namespace": namespace},
        }
        if ready or not_ready:
            subset: Obj = {"ports": [{"port": p["targetPort"]} for p in service["spec"]["ports"]]}
            if ready:
                subset["addresses"] = ready
            if not_ready:
                subset["notReadyAddresses"] = not_ready
            endpoints["subsets"] = [subset]
        return endpoints


def synthesize(scenario_dir: Path) -> None:
    manifests = [
        doc
        for doc in yaml.safe_load_all((scenario_dir / "inject.yaml").read_text(encoding="utf-8"))
        if doc
    ]
    capture = Capture(scenario_dir.name, manifests)

    out = scenario_dir / "captured"
    shutil.rmtree(out, ignore_errors=True)
    (out / "logs").mkdir(parents=True)
    header = "# Synthesized by faults/tools/synthesize_captures.py; not a recording of a live cluster.\n"
    body = yaml.safe_dump_all(capture.objects, sort_keys=False, width=1000)
    (out / "cluster.yaml").write_text(header + body, encoding="utf-8", newline="\n")
    for filename, text in capture.logs.items():
        (out / "logs" / filename).write_text(text, encoding="utf-8", newline="\n")
    # Keeps the directory in git for scenarios whose containers never produced logs.
    (out / "logs" / ".gitkeep").touch()


def main() -> None:
    scenario_dirs = sorted(p.parent for p in FAULTS_DIR.glob("*/inject.yaml"))
    for scenario_dir in scenario_dirs:
        synthesize(scenario_dir)
    print(f"synthesized {len(scenario_dirs)} captures")


if __name__ == "__main__":
    main()

"""Collects what an engineer would look at for a failing workload.

Roughly `kubectl describe` on the workload and its pods, `kubectl get events`,
`kubectl logs [--previous]`, plus the neighbouring objects that commonly explain a
failure: Services, PVCs, ConfigMap references, NetworkPolicies, ResourceQuotas and nodes.
"""

from typing import Any, Literal, NewType

from pydantic import BaseModel

from app.diagnosis.redact import redact_object
from app.kube.reader import PLURALS, ClusterReader, KubeObject, LogUnavailable
from app.kube.status import is_unschedulable, pod_health

TargetKind = Literal["Deployment", "StatefulSet", "DaemonSet", "Pod"]

MAX_PODS = 3
MAX_EVENTS = 40


class Target(BaseModel):
    kind: TargetKind
    namespace: str
    name: str


class PodEvidence(BaseModel):
    name: str
    status: str
    ready: str
    restarts: int
    object: dict[str, Any]


class EventEvidence(BaseModel):
    type: str
    reason: str
    object: str
    message: str
    count: int
    last_seen: str | None


class LogEvidence(BaseModel):
    pod: str
    container: str
    previous: bool
    text: str


class ServiceEvidence(BaseModel):
    name: str
    type: str
    selector: dict[str, str]
    selector_matches_target: bool
    ports: list[dict[str, Any]]
    # Service targetPorts that no container in the target declares.
    undeclared_target_ports: list[str]
    ready_endpoints: int
    not_ready_endpoints: int


class ConfigReference(BaseModel):
    kind: Literal["ConfigMap", "Secret"]
    name: str
    key: str | None = None
    used_as: str
    optional: bool = False
    # None for Secrets: the app cannot read them, so existence is unknown.
    exists: bool | None = None
    key_exists: bool | None = None


class NodeEvidence(BaseModel):
    name: str
    ready: bool
    labels: dict[str, str]
    taints: list[dict[str, Any]]
    allocatable: dict[str, str]


class Evidence(BaseModel):
    target: Target
    workload: dict[str, Any] | None = None
    replica_sets: list[dict[str, Any]] = []
    pods: list[PodEvidence] = []
    events: list[EventEvidence] = []
    logs: list[LogEvidence] = []
    services: list[ServiceEvidence] = []
    config_references: list[ConfigReference] = []
    persistent_volume_claims: list[dict[str, Any]] = []
    network_policies: list[dict[str, Any]] = []
    resource_quotas: list[dict[str, Any]] = []
    nodes: list[NodeEvidence] = []


# The prompt builder only accepts this type, so unredacted evidence cannot be sent by accident.
RedactedEvidence = NewType("RedactedEvidence", Evidence)


class TargetNotFound(Exception):
    pass


_DROPPED_METADATA = ("managedFields", "uid", "resourceVersion", "generation", "selfLink")


def trim(obj: KubeObject) -> dict[str, Any]:
    """Drop bookkeeping fields that `kubectl describe` does not show either."""
    metadata = {k: v for k, v in obj.get("metadata", {}).items() if k not in _DROPPED_METADATA}
    return {**{k: v for k, v in obj.items() if k not in ("apiVersion",)}, "metadata": metadata}


def _name(obj: KubeObject) -> str:
    return obj["metadata"]["name"]


def _owned_by(obj: KubeObject, kind: str, names: set[str]) -> bool:
    return any(
        ref.get("kind") == kind and ref.get("name") in names
        for ref in obj.get("metadata", {}).get("ownerReferences", [])
    )


def _labels_match(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


class EvidenceCollector:
    def __init__(self, reader: ClusterReader, log_tail_lines: int = 80) -> None:
        self._reader = reader
        self._log_tail_lines = log_tail_lines

    def collect(self, target: Target) -> Evidence:
        namespace = target.namespace
        all_pods = self._reader.list("pods", namespace).items

        if target.kind == "Pod":
            pods = [p for p in all_pods if _name(p) == target.name]
            if not pods:
                raise TargetNotFound(f"Pod {namespace}/{target.name} not found")
            workload, replica_sets = None, []
            template = pods[0]
        else:
            workload = self._find(PLURALS[target.kind], target)
            replica_sets = self._replica_sets(target) if target.kind == "Deployment" else []
            pods = self._workload_pods(target, replica_sets, all_pods)
            template = workload["spec"]["template"]

        pods.sort(key=lambda p: (not pod_health(p).failing, _name(p)))
        pods = pods[:MAX_PODS]
        pod_spec = template.get("spec", {})
        template_labels = template.get("metadata", {}).get("labels", {})
        claim_names = self._claim_names(pods)

        involved = {(target.kind, target.name)}
        involved |= {("ReplicaSet", _name(rs)) for rs in replica_sets}
        involved |= {("Pod", _name(p)) for p in pods}
        involved |= {("PersistentVolumeClaim", name) for name in claim_names}

        return Evidence(
            target=target,
            workload=trim(workload) if workload else None,
            replica_sets=[self._replica_set_summary(rs) for rs in replica_sets],
            pods=[self._pod_evidence(p) for p in pods],
            events=self._events(namespace, involved),
            logs=self._logs(pods),
            services=self._services(namespace, template_labels, pod_spec),
            config_references=self._config_references(namespace, pod_spec),
            persistent_volume_claims=[
                trim(pvc)
                for pvc in self._reader.list("persistentvolumeclaims", namespace).items
                if _name(pvc) in claim_names
            ],
            network_policies=[
                {"name": _name(np), "spec": np.get("spec", {})}
                for np in self._reader.list("networkpolicies", namespace).items
            ],
            resource_quotas=[
                {"name": _name(rq), **rq.get("status", {})}
                for rq in self._reader.list("resourcequotas", namespace).items
            ],
            nodes=self._nodes() if any(is_unschedulable(p) for p in pods) else [],
        )

    def _find(self, resource: str, target: Target) -> KubeObject:
        for obj in self._reader.list(resource, target.namespace).items:
            if _name(obj) == target.name:
                return obj
        raise TargetNotFound(f"{target.kind} {target.namespace}/{target.name} not found")

    def _replica_sets(self, target: Target) -> list[KubeObject]:
        owned = [
            rs
            for rs in self._reader.list("replicasets", target.namespace).items
            if _owned_by(rs, "Deployment", {target.name})
        ]
        # Scaled-down ReplicaSets from old rollouts are noise.
        return [rs for rs in owned if rs.get("spec", {}).get("replicas", 0) > 0]

    def _workload_pods(
        self, target: Target, replica_sets: list[KubeObject], pods: list[KubeObject]
    ) -> list[KubeObject]:
        if target.kind == "Deployment":
            owner_kind, owner_names = "ReplicaSet", {_name(rs) for rs in replica_sets}
        else:
            owner_kind, owner_names = target.kind, {target.name}
        return [p for p in pods if _owned_by(p, owner_kind, owner_names)]

    @staticmethod
    def _replica_set_summary(rs: KubeObject) -> dict[str, Any]:
        return {
            "name": _name(rs),
            "desired": rs.get("spec", {}).get("replicas"),
            "status": rs.get("status", {}),
        }

    @staticmethod
    def _pod_evidence(pod: KubeObject) -> PodEvidence:
        health = pod_health(pod)
        return PodEvidence(
            name=_name(pod),
            status=health.reason,
            ready=f"{health.ready}/{health.total}",
            restarts=health.restarts,
            object=trim(pod),
        )

    @staticmethod
    def _claim_names(pods: list[KubeObject]) -> set[str]:
        return {
            volume["persistentVolumeClaim"]["claimName"]
            for pod in pods
            for volume in pod.get("spec", {}).get("volumes", [])
            if "persistentVolumeClaim" in volume
        }

    def _events(self, namespace: str, involved: set[tuple[str, str]]) -> list[EventEvidence]:
        events = []
        for event in self._reader.list("events", namespace).items:
            obj = event.get("involvedObject", {})
            if (obj.get("kind"), obj.get("name")) not in involved:
                continue
            events.append(
                EventEvidence(
                    type=event.get("type", "Normal"),
                    reason=event.get("reason", ""),
                    object=f"{obj.get('kind')}/{obj.get('name')}",
                    message=event.get("message", ""),
                    count=event.get("count") or 1,
                    last_seen=event.get("lastTimestamp") or event.get("eventTime"),
                )
            )
        # Warnings first, then newest first, so truncation drops the least useful events.
        events.sort(key=lambda e: e.last_seen or "", reverse=True)
        events.sort(key=lambda e: e.type != "Warning")
        return events[:MAX_EVENTS]

    def _logs(self, pods: list[KubeObject]) -> list[LogEvidence]:
        logs = []
        for pod in pods:
            status = pod.get("status", {})
            statuses = status.get("initContainerStatuses", []) + status.get("containerStatuses", [])
            for container in statuses:
                wanted = [False]
                if container.get("restartCount", 0) > 0:
                    wanted.append(True)
                for previous in wanted:
                    text = self._read_log(pod, container["name"], previous)
                    if text:
                        logs.append(
                            LogEvidence(
                                pod=_name(pod),
                                container=container["name"],
                                previous=previous,
                                text=text,
                            )
                        )
        return logs

    def _read_log(self, pod: KubeObject, container: str, previous: bool) -> str:
        try:
            return self._reader.read_log(
                pod["metadata"]["namespace"], _name(pod), container, self._log_tail_lines, previous
            )
        except LogUnavailable:
            return ""

    def _services(
        self, namespace: str, template_labels: dict[str, str], pod_spec: dict[str, Any]
    ) -> list[ServiceEvidence]:
        container_ports = [
            port for c in pod_spec.get("containers", []) for port in c.get("ports", [])
        ]
        declared = {str(p["containerPort"]) for p in container_ports if "containerPort" in p}
        declared |= {p["name"] for p in container_ports if "name" in p}
        endpoints = {_name(e): e for e in self._reader.list("endpoints", namespace).items}

        services = []
        for service in self._reader.list("services", namespace).items:
            spec = service.get("spec", {})
            selector = spec.get("selector") or {}
            if not selector:
                continue
            ports = spec.get("ports", [])
            subsets = endpoints.get(_name(service), {}).get("subsets") or []
            services.append(
                ServiceEvidence(
                    name=_name(service),
                    type=spec.get("type", "ClusterIP"),
                    selector=selector,
                    selector_matches_target=_labels_match(selector, template_labels),
                    ports=ports,
                    undeclared_target_ports=[
                        str(p.get("targetPort", p.get("port")))
                        for p in ports
                        if str(p.get("targetPort", p.get("port"))) not in declared
                    ],
                    ready_endpoints=sum(len(s.get("addresses", [])) for s in subsets),
                    not_ready_endpoints=sum(len(s.get("notReadyAddresses", [])) for s in subsets),
                )
            )
        return services

    def _config_references(self, namespace: str, pod_spec: dict[str, Any]) -> list[ConfigReference]:
        config_maps = {
            _name(cm): set(cm.get("data", {})) | set(cm.get("binaryData", {}))
            for cm in self._reader.list("configmaps", namespace).items
        }
        references = _config_references(pod_spec)
        for ref in references:
            if ref.kind != "ConfigMap":
                continue
            ref.exists = ref.name in config_maps
            if ref.key is not None:
                ref.key_exists = ref.key in config_maps.get(ref.name, set())
        return references

    def _nodes(self) -> list[NodeEvidence]:
        nodes = []
        for node in self._reader.list("nodes").items:
            conditions = node.get("status", {}).get("conditions", [])
            nodes.append(
                NodeEvidence(
                    name=_name(node),
                    ready=any(
                        c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
                    ),
                    labels=node["metadata"].get("labels", {}),
                    taints=node.get("spec", {}).get("taints", []),
                    allocatable=node.get("status", {}).get("allocatable", {}),
                )
            )
        return nodes


def _config_references(pod_spec: dict[str, Any]) -> list[ConfigReference]:
    references: list[ConfigReference] = []
    containers = pod_spec.get("initContainers", []) + pod_spec.get("containers", [])
    for container in containers:
        for env in container.get("env", []):
            source = env.get("valueFrom", {})
            for field, kind in (("configMapKeyRef", "ConfigMap"), ("secretKeyRef", "Secret")):
                if field in source:
                    ref = source[field]
                    references.append(
                        ConfigReference(
                            kind=kind,
                            name=ref["name"],
                            key=ref["key"],
                            used_as=f"env {env['name']}",
                            optional=bool(ref.get("optional")),
                        )
                    )
        for env_from in container.get("envFrom", []):
            for field, kind in (("configMapRef", "ConfigMap"), ("secretRef", "Secret")):
                if field in env_from:
                    ref = env_from[field]
                    references.append(
                        ConfigReference(
                            kind=kind,
                            name=ref["name"],
                            used_as="envFrom",
                            optional=bool(ref.get("optional")),
                        )
                    )
    for volume in pod_spec.get("volumes", []):
        if "configMap" in volume:
            source = volume["configMap"]
            references.append(
                ConfigReference(
                    kind="ConfigMap",
                    name=source["name"],
                    used_as=f"volume {volume['name']}",
                    optional=bool(source.get("optional")),
                )
            )
        if "secret" in volume:
            source = volume["secret"]
            references.append(
                ConfigReference(
                    kind="Secret",
                    name=source["secretName"],
                    used_as=f"volume {volume['name']}",
                    optional=bool(source.get("optional")),
                )
            )
    return references


def redact_evidence(evidence: Evidence) -> RedactedEvidence:
    redacted = redact_object(evidence.model_dump())
    return RedactedEvidence(Evidence.model_validate(redacted))

import json
from collections.abc import Iterator

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.watch.watch import iter_resp_lines

from app.kube.reader import (
    ListResult,
    LogUnavailable,
    ResourceVersionExpired,
    WatchEvent,
)

# resource -> (API class, list-all-namespaces method, list-in-namespace method)
_LISTERS: dict[str, tuple[type, str, str | None]] = {
    "pods": (client.CoreV1Api, "list_pod_for_all_namespaces", "list_namespaced_pod"),
    "services": (client.CoreV1Api, "list_service_for_all_namespaces", "list_namespaced_service"),
    "endpoints": (
        client.CoreV1Api,
        "list_endpoints_for_all_namespaces",
        "list_namespaced_endpoints",
    ),
    "events": (client.CoreV1Api, "list_event_for_all_namespaces", "list_namespaced_event"),
    "persistentvolumeclaims": (
        client.CoreV1Api,
        "list_persistent_volume_claim_for_all_namespaces",
        "list_namespaced_persistent_volume_claim",
    ),
    "configmaps": (
        client.CoreV1Api,
        "list_config_map_for_all_namespaces",
        "list_namespaced_config_map",
    ),
    "resourcequotas": (
        client.CoreV1Api,
        "list_resource_quota_for_all_namespaces",
        "list_namespaced_resource_quota",
    ),
    "nodes": (client.CoreV1Api, "list_node", None),
    "deployments": (
        client.AppsV1Api,
        "list_deployment_for_all_namespaces",
        "list_namespaced_deployment",
    ),
    "replicasets": (
        client.AppsV1Api,
        "list_replica_set_for_all_namespaces",
        "list_namespaced_replica_set",
    ),
    "statefulsets": (
        client.AppsV1Api,
        "list_stateful_set_for_all_namespaces",
        "list_namespaced_stateful_set",
    ),
    "daemonsets": (
        client.AppsV1Api,
        "list_daemon_set_for_all_namespaces",
        "list_namespaced_daemon_set",
    ),
    "networkpolicies": (
        client.NetworkingV1Api,
        "list_network_policy_for_all_namespaces",
        "list_namespaced_network_policy",
    ),
}


class KubernetesReader:
    """ClusterReader backed by the official client. Only list, watch and log reads are used."""

    def __init__(self, api_client: client.ApiClient) -> None:
        self._api_client = api_client
        self._core = client.CoreV1Api(api_client)

    @classmethod
    def from_environment(cls, in_cluster: bool) -> "KubernetesReader":
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        return cls(client.ApiClient())

    def _lister(self, resource: str, namespace: str | None):
        api_class, list_all, list_namespaced = _LISTERS[resource]
        api = api_class(self._api_client)
        if namespace and list_namespaced:
            return getattr(api, list_namespaced), {"namespace": namespace}
        return getattr(api, list_all), {}

    def list(self, resource: str, namespace: str | None = None) -> ListResult:
        lister, kwargs = self._lister(resource, namespace)
        # Raw JSON keeps objects in the same shape as `kubectl get -o json` and the fixtures.
        response = lister(_preload_content=False, **kwargs)
        body = json.loads(response.data)
        return ListResult(
            items=body.get("items", []),
            resource_version=body.get("metadata", {}).get("resourceVersion", ""),
        )

    def watch(
        self, resource: str, resource_version: str, timeout_seconds: int
    ) -> Iterator[WatchEvent]:
        # The client's Watch helper is not used: it deserializes into typed models (which
        # rejects bookmark objects for some kinds) and retries a 410 on its own. Reading the
        # stream directly keeps raw objects and leaves resourceVersion handling to the caller.
        lister, kwargs = self._lister(resource, None)
        try:
            response = lister(
                watch=True,
                resource_version=resource_version,
                timeout_seconds=timeout_seconds,
                allow_watch_bookmarks=True,
                _preload_content=False,
                **kwargs,
            )
        except ApiException as exc:
            if exc.status == 410:
                raise ResourceVersionExpired(str(exc.reason)) from exc
            raise
        try:
            for line in iter_resp_lines(response):
                if line:
                    event = json.loads(line)
                    # A 410 on an established watch arrives in-band as a Status object.
                    if event["type"] == "ERROR" and event["object"].get("code") == 410:
                        raise ResourceVersionExpired(event["object"].get("message", ""))
                    yield WatchEvent(type=event["type"], object=event["object"])
        finally:
            response.close()
            response.release_conn()

    def read_log(
        self, namespace: str, pod: str, container: str, tail_lines: int, previous: bool = False
    ) -> str:
        try:
            response = self._core.read_namespaced_pod_log(
                name=pod,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                previous=previous,
                _preload_content=False,
            )
        except ApiException as exc:
            # 400: the container has not started, or there is no previous instance.
            if exc.status in (400, 404):
                raise LogUnavailable(str(exc.reason)) from exc
            raise
        return response.data.decode("utf-8", errors="replace")

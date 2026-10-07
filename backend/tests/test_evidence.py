import pytest

from app.diagnosis.evidence import (
    MAX_PODS,
    Evidence,
    EvidenceCollector,
    Target,
    TargetNotFound,
    redact_evidence,
)
from app.kube.fake import FakeCluster
from tests.conftest import SCENARIOS, capture, scenario


def collect(scenario_id: str) -> Evidence:
    return EvidenceCollector(capture(scenario_id)).collect(scenario(scenario_id).diagnosis_target)


@pytest.mark.parametrize("item", SCENARIOS, ids=lambda s: s.id)
def test_evidence_contains_the_signals_for_each_fault(item):
    evidence = redact_evidence(collect(item.id))
    text = evidence.model_dump_json()

    assert evidence.workload is not None
    missing = [signal for signal in item.expected_signals if signal not in text.replace('\\"', '"')]
    assert not missing, f"signals not collected: {missing}"


def test_library_covers_twenty_distinct_root_causes():
    assert len(SCENARIOS) == 20
    assert len({s.expected_category for s in SCENARIOS}) == 20


def test_service_selector_mismatch_is_computed_against_the_pod_template():
    evidence = collect("service-selector-mismatch")
    service = evidence.services[0]

    assert service.selector == {"app": "web-frontend"}
    assert service.selector_matches_target is False
    assert service.ready_endpoints == 0
    assert all(pod.status == "Running" for pod in evidence.pods)


def test_service_port_mismatch_lists_the_undeclared_target_port():
    service = collect("wrong-container-port").services[0]

    assert service.selector_matches_target is True
    assert service.undeclared_target_ports == ["8080"]
    assert service.ready_endpoints == 2


def test_healthy_service_has_no_findings():
    service = collect("readiness-probe-failing").services[0]

    assert service.selector_matches_target is True
    assert service.undeclared_target_ports == []
    assert (service.ready_endpoints, service.not_ready_endpoints) == (0, 2)


def test_crash_looping_container_gets_current_and_previous_logs():
    evidence = collect("oom-killed")

    assert [(log.container, log.previous) for log in evidence.logs] == [
        ("worker", False),
        ("worker", True),
    ]
    assert evidence.pods[0].status == "CrashLoopBackOff"
    assert evidence.pods[0].restarts == 6
    terminated = evidence.pods[0].object["status"]["containerStatuses"][0]["lastState"][
        "terminated"
    ]
    assert (terminated["reason"], terminated["exitCode"]) == ("OOMKilled", 137)


def test_container_that_never_started_has_no_logs():
    evidence = collect("bad-image-tag")
    assert evidence.logs == []
    assert evidence.pods[0].status == "ImagePullBackOff"


def test_missing_configmap_key_is_resolved_against_existing_keys():
    references = {r.key: r for r in collect("missing-configmap-key").config_references}

    assert (references["LOG_LEVEL"].exists, references["LOG_LEVEL"].key_exists) == (True, True)
    assert (references["APP_MODE"].exists, references["APP_MODE"].key_exists) == (True, False)


def test_missing_configmap_volume_is_reported_as_absent():
    reference = collect("missing-configmap").config_references[0]

    assert (reference.kind, reference.name, reference.exists) == (
        "ConfigMap",
        "edge-proxy-conf",
        False,
    )
    assert reference.used_as == "volume config"


def test_secret_references_are_listed_with_unknown_existence():
    reference = collect("missing-secret").config_references[0]

    assert (reference.kind, reference.name, reference.key) == (
        "Secret",
        "billing-db-credentials",
        "password",
    )
    assert reference.exists is None


def test_configmap_values_are_never_collected():
    text = collect("missing-configmap-key").model_dump_json()
    assert "checkout-v2" not in text


def test_pending_pvc_and_its_events_are_included():
    evidence = collect("pvc-pending")

    claim = evidence.persistent_volume_claims[0]
    assert claim["status"]["phase"] == "Pending"
    assert claim["spec"]["storageClassName"] == "fast-ssd"
    assert any(e.object == "PersistentVolumeClaim/uploads" for e in evidence.events)


@pytest.mark.parametrize(
    "scenario_id", ["unschedulable-resources", "node-selector-mismatch", "untolerated-taint"]
)
def test_unschedulable_pods_bring_node_details(scenario_id):
    evidence = collect(scenario_id)
    nodes = {n.name: n for n in evidence.nodes}

    assert evidence.pods[0].status == "Unschedulable"
    assert len(nodes) == 3
    assert nodes["kube-explorer-worker2"].labels["dedicated"] == "batch"
    assert nodes["kube-explorer-worker2"].taints[0]["key"] == "dedicated"
    assert nodes["kube-explorer-worker"].allocatable["memory"] == "16304520Ki"


def test_nodes_are_left_out_when_pods_are_scheduled():
    assert collect("oom-killed").nodes == []


def test_quota_scenario_surfaces_replicaset_failure_and_quota_usage():
    evidence = collect("quota-exceeded")

    assert len(evidence.pods) == 2
    quota = evidence.resource_quotas[0]
    assert quota["hard"]["requests.cpu"] == quota["used"]["requests.cpu"] == "500m"
    failed = [e for e in evidence.events if e.reason == "FailedCreate"]
    assert failed and failed[0].object.startswith("ReplicaSet/api-")
    assert evidence.events[0].type == "Warning", "warnings sort ahead of normal events"


def test_network_policies_in_the_namespace_are_included():
    evidence = collect("netpol-blocks-dns")

    assert [p["name"] for p in evidence.network_policies] == ["checkout-egress"]
    assert evidence.network_policies[0]["spec"]["policyTypes"] == ["Egress"]
    # The neighbouring payments Deployment is not part of the checkout evidence.
    assert all(pod.name.startswith("checkout-") for pod in evidence.pods)


def test_init_container_failure_collects_init_logs_and_status():
    evidence = collect("init-container-failing")

    assert evidence.pods[0].status == "Init:CrashLoopBackOff"
    assert {log.container for log in evidence.logs} == {"migrate"}


def test_pod_can_be_diagnosed_directly():
    cluster = capture("oom-killed")
    pod_name = cluster.objects["pods"][0]["metadata"]["name"]
    target = Target(kind="Pod", namespace="fault-oom-killed", name=pod_name)

    evidence = EvidenceCollector(cluster).collect(target)

    assert evidence.workload is None
    assert [p.name for p in evidence.pods] == [pod_name]
    assert any(e.reason == "BackOff" for e in evidence.events)


def test_unknown_target_raises():
    collector = EvidenceCollector(capture("oom-killed"))
    with pytest.raises(TargetNotFound):
        collector.collect(Target(kind="Deployment", namespace="fault-oom-killed", name="nope"))
    with pytest.raises(TargetNotFound):
        collector.collect(Target(kind="Pod", namespace="fault-oom-killed", name="nope"))


def test_failing_pods_are_preferred_when_truncating():
    def pod(name: str, ready: bool) -> dict:
        return {
            "metadata": {
                "name": name,
                "namespace": "shop",
                "ownerReferences": [{"kind": "StatefulSet", "name": "db"}],
            },
            "spec": {"containers": [{"name": "db"}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "db", "ready": ready, "restartCount": 0, "state": {"running": {}}}
                ],
            },
        }

    stateful_set = {
        "metadata": {"name": "db", "namespace": "shop"},
        "spec": {"replicas": 6, "template": {"metadata": {"labels": {"app": "db"}}, "spec": {}}},
    }
    pods = [pod(f"db-{n}", ready=n != 4) for n in range(6)]
    cluster = FakeCluster({"statefulsets": [stateful_set], "pods": pods})

    evidence = EvidenceCollector(cluster).collect(
        Target(kind="StatefulSet", namespace="shop", name="db")
    )

    assert len(evidence.pods) == MAX_PODS
    assert evidence.pods[0].name == "db-4"


def test_log_tail_is_limited():
    evidence = EvidenceCollector(capture("netpol-blocks-dns"), log_tail_lines=3).collect(
        scenario("netpol-blocks-dns").diagnosis_target
    )
    assert len(evidence.logs[0].text.splitlines()) == 3

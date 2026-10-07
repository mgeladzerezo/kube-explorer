import pytest

from app.kube.status import pod_health, workload_health
from tests.conftest import capture

EXPECTED = {
    "oom-killed": ("CrashLoopBackOff", True),
    "readiness-probe-failing": ("Running", True),
    "missing-configmap-key": ("CreateContainerConfigError", True),
    "bad-image-tag": ("ImagePullBackOff", True),
    "pvc-pending": ("Unschedulable", True),
    "init-container-failing": ("Init:CrashLoopBackOff", True),
    "missing-configmap": ("ContainerCreating", False),
    "service-selector-mismatch": ("Running", False),
}


@pytest.mark.parametrize(("scenario_id", "expected"), EXPECTED.items())
def test_pod_status_matches_what_kubectl_would_show(scenario_id, expected):
    health = pod_health(capture(scenario_id).objects["pods"][0])
    assert (health.reason, health.failing) == expected


def test_terminating_pod_is_not_failing():
    pod = capture("oom-killed").objects["pods"][0]
    pod["metadata"]["deletionTimestamp"] = "2026-09-28T09:30:00Z"
    assert pod_health(pod).reason == "Terminating"
    assert pod_health(pod).failing is False


def test_freshly_created_pod_is_pending_not_failing():
    health = pod_health({"metadata": {"name": "new"}, "spec": {}, "status": {"phase": "Pending"}})
    assert (health.reason, health.failing) == ("Pending", False)


def test_deployment_short_of_replicas_is_failing():
    deployment = capture("quota-exceeded").objects["deployments"][0]
    health = workload_health("Deployment", deployment)
    assert (health.desired, health.ready, health.failing) == (4, 2, True)


def test_daemonset_uses_its_own_status_fields():
    daemon_set = {"status": {"desiredNumberScheduled": 3, "numberReady": 3}}
    assert workload_health("DaemonSet", daemon_set).failing is False

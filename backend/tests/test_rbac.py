"""The Helm ClusterRole and the code must agree on what the app may read."""

import re

from app.kube.reader import KINDS
from tests.conftest import BACKEND_DIR

CLUSTER_ROLE = BACKEND_DIR.parent / "deploy/helm/kube-explorer/templates/clusterrole.yaml"


def flow_lists(key: str) -> list[list[str]]:
    text = CLUSTER_ROLE.read_text(encoding="utf-8")
    return [re.findall(r'"([^"]*)"', match) for match in re.findall(rf"{key}: \[(.*)\]", text)]


def test_cluster_role_grants_exactly_the_resources_the_reader_uses():
    granted = {resource for rule in flow_lists("resources") for resource in rule}
    assert granted == set(KINDS) | {"pods/log"}


def test_cluster_role_is_read_only_and_cannot_read_secrets():
    verbs = flow_lists("verbs")
    assert verbs and all(rule == ["get", "list", "watch"] for rule in verbs)
    assert "secrets" not in CLUSTER_ROLE.read_text(encoding="utf-8").split("rules:")[1]

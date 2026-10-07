import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.diagnosis.llm import ModelError
from app.kube.reader import WatchEvent
from app.main import create_app
from tests.conftest import ScriptedModel, capture, diagnosis_json

OOM_TARGET = {"kind": "Deployment", "namespace": "fault-oom-killed", "name": "report-worker"}


def make_client(session_factory, model=None, reader=None, start_watchers=False) -> TestClient:
    settings = Settings(kube_mode="fixture", llm_mode="fake", watch_timeout_seconds=0)
    app = create_app(
        settings=settings,
        reader=reader or capture("oom-killed"),
        model=model or ScriptedModel(diagnosis_json()),
        session_factory=session_factory,
        start_watchers=start_watchers,
    )
    return TestClient(app)


def test_health_and_metrics(session_factory):
    with make_client(session_factory) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}
        metrics = client.get("/metrics").text
        assert "kube_explorer_http_requests_total" in metrics
        assert "kube_explorer_watch_restarts_total" in metrics


def test_diagnosis_is_returned_and_stored(session_factory):
    with make_client(session_factory) as client:
        response = client.post("/api/diagnoses", json=OOM_TARGET)

        assert response.status_code == 201
        body = response.json()
        assert body["model"] == "scripted"
        assert body["diagnosis"]["hypotheses"][0]["category"] == "oom_killed"
        assert body["diagnosis"]["hypotheses"][0]["command_is_read_only"] is True
        assert body["evidence"]["pods"][0]["status"] == "CrashLoopBackOff"

        stored = client.get("/api/diagnoses", params={"namespace": "fault-oom-killed"}).json()
        assert [d["id"] for d in stored] == [body["id"]]
        assert stored[0]["evidence"] is None


def test_unknown_target_is_404(session_factory):
    with make_client(session_factory) as client:
        response = client.post("/api/diagnoses", json={**OOM_TARGET, "name": "missing"})
        assert response.status_code == 404


def test_unsupported_kind_is_rejected(session_factory):
    with make_client(session_factory) as client:
        response = client.post("/api/diagnoses", json={**OOM_TARGET, "kind": "Secret"})
        assert response.status_code == 422


def test_malformed_answer_is_retried_once(session_factory):
    model = ScriptedModel("not json at all", diagnosis_json())
    with make_client(session_factory, model) as client:
        assert client.post("/api/diagnoses", json=OOM_TARGET).status_code == 201
    assert len(model.prompts) == 2


def test_repeated_malformed_answers_become_502_and_nothing_is_stored(session_factory):
    model = ScriptedModel('{"summary": "x", "hypotheses": []}')
    with make_client(session_factory, model) as client:
        response = client.post("/api/diagnoses", json=OOM_TARGET)

        assert response.status_code == 502
        assert "unusable answer" in response.json()["detail"]
        assert client.get("/api/diagnoses").json() == []


@pytest.mark.parametrize(("retryable", "status"), [(True, 503), (False, 502)])
def test_model_failures_map_to_gateway_errors(session_factory, retryable, status):
    model = ScriptedModel(ModelError("upstream said no", retryable=retryable))
    with make_client(session_factory, model) as client:
        assert client.post("/api/diagnoses", json=OOM_TARGET).status_code == status


def test_evidence_endpoint_returns_redacted_evidence(session_factory):
    with make_client(session_factory, reader=capture("init-container-failing")) as client:
        response = client.get(
            "/api/evidence",
            params={
                "kind": "Deployment",
                "namespace": "fault-init-container-failing",
                "name": "inventory",
            },
        )
        assert response.status_code == 200
        assert "Tr0ub4dor-inventory" not in response.text
        assert "inventory-db:5432" in response.text


def test_logs_endpoint_redacts_and_reports_missing_logs(session_factory):
    reader = capture("init-container-failing")
    pod = reader.objects["pods"][0]["metadata"]["name"]
    base = f"/api/pods/fault-init-container-failing/{pod}/logs"
    with make_client(session_factory, reader=reader) as client:
        logs = client.get(base, params={"container": "migrate"}).json()
        assert logs["available"] is True
        assert "Tr0ub4dor-inventory" not in logs["text"]
        assert "0001_init.sql is missing" in logs["text"]

        missing = client.get(base, params={"container": "inventory"}).json()
        assert missing == {"text": "", "available": False}


class GatedReader:
    """Serves a capture, then emits one pod change when the test opens the gate."""

    def __init__(self, cluster) -> None:
        self._cluster = cluster
        self.gate = threading.Event()
        self._sent = False

    def list(self, resource, namespace=None):
        return self._cluster.list(resource, namespace)

    def read_log(self, *args, **kwargs):
        return self._cluster.read_log(*args, **kwargs)

    def watch(self, resource, resource_version, timeout_seconds):
        if resource != "pods" or self._sent:
            return
        self.gate.wait(timeout=10)
        self._sent = True
        pod = self._cluster.objects["pods"][0]
        recovered = {
            **pod,
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "worker", "ready": True, "restartCount": 6, "state": {"running": {}}}
                ],
            },
        }
        yield WatchEvent("MODIFIED", recovered)


def test_websocket_sends_snapshot_then_live_changes(session_factory):
    reader = GatedReader(capture("oom-killed"))
    pods: dict[str, dict] = {}

    def fold(message: dict) -> None:
        if message["type"] == "snapshot":
            pods.update({p["id"]: p for p in message["collections"]["pods"]})
        elif message["type"] == "upsert" and message["collection"] == "pods":
            pods[message["item"]["id"]] = message["item"]

    with (
        make_client(session_factory, reader=reader, start_watchers=True) as client,
        client.websocket_connect("/ws/cluster") as socket,
    ):
        first = socket.receive_json()
        assert first["type"] == "snapshot"
        fold(first)
        # The initial list may land just before or just after the socket connects.
        while not pods:
            fold(socket.receive_json())
        (pod,) = pods.values()
        assert (pod["status"], pod["failing"]) == ("CrashLoopBackOff", True)

        reader.gate.set()
        while pod["failing"]:
            fold(socket.receive_json())
            (pod,) = pods.values()
        assert (pod["status"], pod["ready"], pod["restarts"]) == ("Running", 1, 6)


def test_rest_snapshot_matches_the_websocket_shape(session_factory):
    with make_client(session_factory) as client:
        body = client.get("/api/cluster").json()
        assert body["type"] == "snapshot"
        assert set(body["collections"]) == {"pods", "workloads", "events"}

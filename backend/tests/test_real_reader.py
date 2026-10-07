"""Exercises KubernetesReader against a local HTTP stub that speaks just enough of the API."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from kubernetes import client

from app.kube.reader import LogUnavailable, ResourceVersionExpired
from app.kube.real import KubernetesReader

POD = {"metadata": {"name": "api-1", "namespace": "shop", "resourceVersion": "90"}}
WATCH_LINES = [
    {
        "type": "MODIFIED",
        "object": {**POD, "metadata": {**POD["metadata"], "resourceVersion": "101"}},
    },
    {"type": "BOOKMARK", "object": {"kind": "Pod", "metadata": {"resourceVersion": "140"}}},
]
GONE = {
    "type": "ERROR",
    "object": {
        "kind": "Status",
        "status": "Failure",
        "reason": "Expired",
        "code": 410,
        "message": "too old resource version: 1 (5000)",
    },
}


class StubApiServer(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict]] = []

    def log_message(self, *args) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        # The client sends Python-style booleans ("True"), which the API server accepts.
        query = {
            k: v[0].lower() if v[0] in ("True", "False") else v[0]
            for k, v in parse_qs(url.query).items()
        }
        StubApiServer.requests.append((url.path, query))

        if url.path.endswith("/log"):
            if query.get("previous") == "true":
                status = {"kind": "Status", "code": 400, "reason": "BadRequest", "message": "none"}
                self._send(400, json.dumps(status).encode())
            else:
                self._send(200, b"line one\nline two\n", "text/plain")
        elif query.get("watch") == "true":
            lines = [GONE] if query.get("resourceVersion") == "1" else WATCH_LINES
            self._send(200, "".join(json.dumps(line) + "\n" for line in lines).encode())
        else:
            items = [POD] if url.path in ("/api/v1/pods", "/api/v1/namespaces/shop/pods") else []
            body = {"kind": "List", "metadata": {"resourceVersion": "100"}, "items": items}
            self._send(200, json.dumps(body).encode())


@pytest.fixture
def reader():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubApiServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    StubApiServer.requests = []
    configuration = client.Configuration()
    configuration.host = f"http://127.0.0.1:{server.server_port}"
    yield KubernetesReader(client.ApiClient(configuration))
    server.shutdown()
    server.server_close()


def test_list_returns_raw_objects_and_the_collection_version(reader):
    result = reader.list("pods")

    assert result.items == [POD]
    assert result.resource_version == "100"
    assert StubApiServer.requests[-1][0] == "/api/v1/pods"


def test_list_can_be_scoped_to_a_namespace(reader):
    reader.list("pods", "shop")
    reader.list("networkpolicies", "shop")
    reader.list("nodes", "shop")

    paths = [path for path, _ in StubApiServer.requests]
    assert paths == [
        "/api/v1/namespaces/shop/pods",
        "/apis/networking.k8s.io/v1/namespaces/shop/networkpolicies",
        "/api/v1/nodes",
    ]


def test_watch_resumes_from_the_given_version_and_yields_raw_events(reader):
    events = list(reader.watch("pods", "100", timeout_seconds=5))

    assert [e.type for e in events] == ["MODIFIED", "BOOKMARK"]
    assert events[0].object["metadata"]["resourceVersion"] == "101"
    assert events[1].object["metadata"]["resourceVersion"] == "140"
    _, query = StubApiServer.requests[-1]
    assert query["resourceVersion"] == "100"
    assert query["allowWatchBookmarks"] == "true"
    assert query["timeoutSeconds"] == "5"


def test_expired_resource_version_is_reported_as_such(reader):
    with pytest.raises(ResourceVersionExpired):
        list(reader.watch("pods", "1", timeout_seconds=5))


def test_logs_are_read_with_tail_and_missing_previous_logs_are_distinguished(reader):
    assert reader.read_log("shop", "api-1", "app", tail_lines=50) == "line one\nline two\n"
    path, query = StubApiServer.requests[-1]
    assert path == "/api/v1/namespaces/shop/pods/api-1/log"
    assert (query["container"], query["tailLines"]) == ("app", "50")

    with pytest.raises(LogUnavailable):
        reader.read_log("shop", "api-1", "app", tail_lines=50, previous=True)


def test_only_read_requests_are_ever_sent(reader):
    reader.list("deployments")
    list(reader.watch("events", "100", timeout_seconds=1))
    # The stub only implements GET; anything else would have failed with 501.
    assert all(path.startswith(("/api/", "/apis/")) for path, _ in StubApiServer.requests)

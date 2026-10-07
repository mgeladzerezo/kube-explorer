from prometheus_client import Counter, Gauge, Histogram

WATCH_EVENTS = Counter(
    "kube_explorer_watch_events_total", "Watch events received", ["resource", "type"]
)
WATCH_RESTARTS = Counter(
    "kube_explorer_watch_restarts_total",
    "Watch streams re-established, by cause",
    ["resource", "reason"],
)
WS_CLIENTS = Gauge("kube_explorer_websocket_clients", "Connected browser WebSocket clients")
WS_DROPPED = Counter(
    "kube_explorer_websocket_dropped_total", "Clients disconnected for falling behind"
)
DIAGNOSES = Counter("kube_explorer_diagnoses_total", "Diagnosis requests", ["outcome"])
DIAGNOSIS_SECONDS = Histogram(
    "kube_explorer_diagnosis_duration_seconds",
    "End-to-end diagnosis time",
    buckets=(1, 2, 5, 10, 20, 40, 80, 160),
)
REDACTIONS = Counter("kube_explorer_redactions_total", "Values replaced by the redactor", ["rule"])
HTTP_REQUESTS = Counter(
    "kube_explorer_http_requests_total", "HTTP requests", ["method", "path", "status"]
)

import pytest

from app.diagnosis.evidence import EvidenceCollector, Target, redact_evidence
from app.diagnosis.prompt import build_user_prompt
from app.diagnosis.redact import (
    REDACTED,
    RedactionError,
    assert_clean,
    find_leaks,
    redact_object,
    redact_text,
)
from app.diagnosis.service import Diagnoser
from tests.conftest import SCENARIOS, ScriptedModel, diagnosis_json

# Every credential planted in tests/fixtures/leaky.
CANARIES = [
    "S3cr3t-Passw0rd!",
    "pg-Pa55word",
    "r3dis-pw",
    "sk_live_51MqxExampleKeyValue00",
    "hs256-signing-key-123",
    "smtp-Pa55-separate-arg",
    "probe-tok-12345",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "c2lnbmF0dXJlLXZhbHVl",
    "eyJhbGciOiJSUzI1NiJ9",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "Sql-Adm1n-pw",
    "cGF5bWVudHM6aHVudGVyMg==",
    "xak-9f8e7d6c5b4a",
    "ghp_0123456789abcdefghijABCDEFGHIJ012345",
    "ExamplePrivateKeyMaterialLine",
    "json-Smtp-pw",
]

# Non-secret context the diagnosis needs; redaction must leave it alone.
KEPT = [
    "payments-db.shop.svc:5432",
    "payments-api-token",
    "payments-tls",
    "password authentication failed",
    "LOG_LEVEL",
    "CrashLoopBackOff",
    "registry.example.com/shop/payments-api:2.8.1",
    "sql.internal:1433",
]

TARGET = Target(kind="Deployment", namespace="shop", name="payments-api")


def leaky_prompt(cluster) -> str:
    evidence = redact_evidence(EvidenceCollector(cluster).collect(TARGET))
    return build_user_prompt(evidence)


def test_fixture_actually_contains_the_canaries(leaky_cluster):
    raw = EvidenceCollector(leaky_cluster).collect(TARGET).model_dump_json()
    missing = [c for c in CANARIES if c not in raw and c.replace("/", "\\/") not in raw]
    assert not missing, "fixture drifted; canaries not present before redaction"


@pytest.mark.parametrize("canary", CANARIES)
def test_no_canary_reaches_the_prompt(leaky_cluster, canary):
    assert canary not in leaky_prompt(leaky_cluster)


@pytest.mark.parametrize("kept", KEPT)
def test_diagnostic_context_survives(leaky_cluster, kept):
    assert kept in leaky_prompt(leaky_cluster)


def test_model_only_ever_sees_redacted_text(leaky_cluster):
    model = ScriptedModel(diagnosis_json())
    result = Diagnoser(EvidenceCollector(leaky_cluster), model).diagnose(TARGET)

    sent = model.prompts[0]
    assert not [c for c in CANARIES if c in sent]
    returned = result.model_dump_json()
    assert not [c for c in CANARIES if c in returned]


def test_last_applied_configuration_and_managed_fields_are_dropped(leaky_cluster):
    prompt = leaky_prompt(leaky_cluster)
    assert "last-applied-configuration" not in prompt
    assert "managedFields" not in prompt
    assert "deployment.kubernetes.io/revision" in prompt


def test_secret_references_keep_names_but_never_values(leaky_cluster):
    evidence = redact_evidence(EvidenceCollector(leaky_cluster).collect(TARGET))
    env = {
        e["name"]: e for e in evidence.workload["spec"]["template"]["spec"]["containers"][0]["env"]
    }

    assert env["DB_PASSWORD"]["value"] == REDACTED
    assert env["STRIPE_API_KEY"]["value"] == REDACTED
    assert env["LOG_LEVEL"]["value"] == "debug"
    assert env["API_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "payments-api-token",
        "key": "token",
    }
    # URLs keep the host, which is often the clue, and lose only the credentials.
    assert env["DATABASE_URL"]["value"] == (
        f"postgresql://{REDACTED}@payments-db.shop.svc:5432/payments?sslmode=require"
    )
    assert env["REDIS_URL"]["value"] == f"redis://{REDACTED}@redis.shop.svc:6379/0"


def test_secret_passed_as_separate_argument_is_hidden(leaky_cluster):
    evidence = redact_evidence(EvidenceCollector(leaky_cluster).collect(TARGET))
    args = evidence.workload["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args == ["--listen=:8080", f"--jwt-secret={REDACTED}", "--smtp-password", REDACTED]


def test_secret_objects_are_redacted_wholesale():
    secret = {
        "kind": "Secret",
        "metadata": {"name": "db"},
        "type": "Opaque",
        "data": {"username": "YXBw", "password": "aHVudGVyMg=="},
        "stringData": {"dsn": "host=db user=app"},
    }
    redacted = redact_object(secret)
    assert redacted["data"] == {"username": REDACTED, "password": REDACTED}
    assert redacted["stringData"] == {"dsn": REDACTED}
    assert redacted["metadata"] == {"name": "db"}


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-123_456", "abc.def-123_456"),
        ("authorization=Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ('"Proxy-Authorization": "Digest dGVzdA=="', "dGVzdA=="),
        ("Authorization: 0a1b2c3d4e5f", "0a1b2c3d4e5f"),
        ("got header bearer 9f8e7d6c5b4a3210", "9f8e7d6c5b4a3210"),
        ("mongodb+srv://admin:p%40ss@cluster0.example.net/db", "p%40ss"),
        ("amqp://guest:gu@st@rabbit:5672/", "gu@st"),
        ("Server=db;Database=app;User Id=sa;Password=Pa55;", "Pa55"),
        ("host=db port=5432 user=app password=libpq-pw dbname=app", "libpq-pw"),
        ("PGPASSWORD=env-pw psql -h db", "env-pw"),
        ("client_secret: oauth-shh", "oauth-shh"),
        ("https://api.example.com/v1?api_key=q-key-1&page=2", "q-key-1"),
        ("--token abc123def456", "abc123def456"),
        ("AccountKey=bXlzdG9yYWdla2V5;EndpointSuffix=core.windows.net", "bXlzdG9yYWdla2V5"),
        ("key sk-ant-api03-abcdefghij1234567890 rejected", "sk-ant-api03-abcdefghij1234567890"),
        ("slack xoxb-1234567890-abcdefghij failed", "xoxb-1234567890-abcdefghij"),
    ],
)
def test_text_rules(text, secret):
    redacted = redact_text(text)
    assert secret not in redacted
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "text",
    [
        'Error: secret "billing-db-credentials" not found',
        "couldn't find key APP_MODE in ConfigMap shop/app-config",
        'MountVolume.SetUp failed for volume "token-volume" : configmap "x" not found',
        'Liveness probe failed: Get "http://10.244.1.10:8080/": connection refused',
        "0/3 nodes are available: 1 node(s) had untolerated taint {dedicated: batch}",
        'password authentication failed for user "app"',
        "http://payments.shop.svc.cluster.local:8080/healthz",
    ],
)
def test_ordinary_cluster_messages_are_untouched(text):
    assert redact_text(text) == text


def test_redaction_is_idempotent(leaky_cluster):
    log = leaky_cluster.read_log("shop", "payments-api-7c9d8f6b5-x2kfp", "api", 200)
    once = redact_text(log)
    assert redact_text(once) == once


def test_final_check_fails_closed():
    assert find_leaks("nothing to see") == []
    with pytest.raises(RedactionError, match="url-credentials"):
        assert_clean("dsn postgres://app:hunter2@db/app")
    with pytest.raises(RedactionError, match="jwt"):
        assert_clean("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_fault_library_prompts_pass_the_final_check(scenario):
    from app.kube.fake import load_capture

    collector = EvidenceCollector(load_capture(scenario.capture_dir))
    prompt = build_user_prompt(redact_evidence(collector.collect(scenario.diagnosis_target)))
    assert "Tr0ub4dor-inventory" not in prompt

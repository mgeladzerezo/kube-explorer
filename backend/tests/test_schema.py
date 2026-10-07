import json

import pytest

from app.diagnosis.schema import (
    DIAGNOSIS_JSON_SCHEMA,
    MAX_HYPOTHESES,
    DiagnosisParseError,
    Hypothesis,
    RootCause,
    is_read_only_command,
    parse_diagnosis,
)
from tests.conftest import diagnosis_json


def hypothesis(**overrides) -> dict:
    base = {
        "rank": 1,
        "category": "oom_killed",
        "title": "Out of memory",
        "confidence": 0.7,
        "explanation": "The container passes its limit.",
        "evidence": ["reason: OOMKilled"],
        "next_command": "kubectl top pod -n shop",
    }
    return {**base, **overrides}


def payload(*hypotheses: dict) -> str:
    return json.dumps({"summary": "s", "hypotheses": list(hypotheses) or [hypothesis()]})


def test_valid_response_is_parsed():
    diagnosis = parse_diagnosis(diagnosis_json())

    assert diagnosis.top.category is RootCause.OOM_KILLED
    assert diagnosis.top.rank == 1
    assert diagnosis.top.command_is_read_only is True


@pytest.mark.parametrize(
    "wrapper",
    ["```json\n{}\n```", "```\n{}\n```", "Here is the diagnosis:\n{}\nLet me know.", "  {}  \n"],
)
def test_fences_and_surrounding_prose_are_tolerated(wrapper):
    assert parse_diagnosis(wrapper.replace("{}", diagnosis_json())).top.category == "oom_killed"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "no JSON object"),
        ("I could not determine the cause.", "no JSON object"),
        ('{"summary": "cut off", "hypotheses": [{"rank": 1,', "no JSON object"),
        ('{"summary": "x", "hypotheses": [}', "not valid JSON"),
        ("[1, 2, 3]", "no JSON object"),
        ('{"summary": "x"}', "hypotheses"),
        (json.dumps({"summary": "x", "hypotheses": []}), "hypotheses"),
        (json.dumps({"summary": "", "hypotheses": [hypothesis()]}), "summary"),
        (payload(hypothesis(category="gremlins")), "category"),
        (payload(hypothesis(confidence=1.4)), "confidence"),
        (payload(hypothesis(confidence=-0.1)), "confidence"),
        (payload(hypothesis(confidence="high")), "confidence"),
        (payload(hypothesis(rank=0)), "rank"),
        (payload(hypothesis(evidence=[])), "evidence"),
        (payload(hypothesis(evidence=["  ", ""])), "evidence"),
        (payload(hypothesis(evidence="OOMKilled")), "evidence"),
        (payload(hypothesis(next_command="")), "next_command"),
        (
            payload(hypothesis(next_command="kubectl get pods\nkubectl delete pod x")),
            "next_command",
        ),
        (payload({k: v for k, v in hypothesis().items() if k != "explanation"}), "explanation"),
        (json.dumps({"summary": "x", "hypotheses": "none"}), "hypotheses"),
    ],
)
def test_malformed_responses_are_rejected(raw, message):
    with pytest.raises(DiagnosisParseError, match=message):
        parse_diagnosis(raw)


def test_hypotheses_are_ordered_by_rank_and_renumbered():
    raw = payload(
        hypothesis(rank=3, category="bad_command", confidence=0.1),
        hypothesis(rank=1, category="oom_killed", confidence=0.6),
        hypothesis(rank=7, category="other", confidence=0.05),
    )
    diagnosis = parse_diagnosis(raw)

    assert [h.category.value for h in diagnosis.hypotheses] == [
        "oom_killed",
        "bad_command",
        "other",
    ]
    assert [h.rank for h in diagnosis.hypotheses] == [1, 2, 3]


def test_duplicate_ranks_fall_back_to_confidence():
    raw = payload(
        hypothesis(rank=1, category="bad_command", confidence=0.2),
        hypothesis(rank=1, category="oom_killed", confidence=0.7),
    )
    assert parse_diagnosis(raw).top.category is RootCause.OOM_KILLED


def test_extra_hypotheses_are_dropped():
    raw = payload(*[hypothesis(rank=n) for n in range(1, MAX_HYPOTHESES + 4)])
    assert len(parse_diagnosis(raw).hypotheses) == MAX_HYPOTHESES


def test_unknown_fields_are_ignored():
    raw = payload(hypothesis(severity="high"))
    assert parse_diagnosis(raw).top.title == "Out of memory"


@pytest.mark.parametrize(
    ("command", "read_only"),
    [
        ("kubectl get pods -n shop", True),
        ("kubectl -n shop describe deployment/api", True),
        ("kubectl logs api-123 -c app --previous -n shop", True),
        ("kubectl auth can-i list pods --as system:serviceaccount:shop:api", True),
        ("kubectl rollout status deployment/api -n shop", True),
        ("kubectl --context prod -n shop get events", True),
        ("kubectl delete pod api-123 -n shop", False),
        ("kubectl rollout restart deployment/api", False),
        ("kubectl -n shop exec api-123 -- cat /etc/passwd", False),
        ("kubectl apply -f fix.yaml", False),
        ("kubectl get pods; kubectl delete ns shop", False),
        ("kubectl get pods | xargs kubectl delete pod", False),
        ("kubectl get secret db -o yaml > out.txt", False),
        ("helm rollback api", False),
        ("kubectl", False),
    ],
)
def test_read_only_command_check(command, read_only):
    assert is_read_only_command(command) is read_only


def test_mutating_suggestion_is_flagged_not_rejected():
    raw = payload(hypothesis(next_command="kubectl rollout restart deployment/api -n shop"))
    assert parse_diagnosis(raw).top.command_is_read_only is False


def test_api_schema_matches_the_pydantic_model():
    item = DIAGNOSIS_JSON_SCHEMA["properties"]["hypotheses"]["items"]

    assert item["properties"]["category"]["enum"] == [c.value for c in RootCause]
    assert set(item["required"]) == set(item["properties"]) == set(Hypothesis.model_fields)
    assert item["additionalProperties"] is False

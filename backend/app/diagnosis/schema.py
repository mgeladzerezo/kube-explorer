import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, computed_field, field_validator

MAX_HYPOTHESES = 5


class RootCause(StrEnum):
    """Closed set of root causes. The eval harness scores on this field, not on free text."""

    SERVICE_SELECTOR_MISMATCH = "service_selector_mismatch"
    SERVICE_PORT_MISMATCH = "service_port_mismatch"
    OOM_KILLED = "oom_killed"
    READINESS_PROBE_FAILING = "readiness_probe_failing"
    LIVENESS_PROBE_FAILING = "liveness_probe_failing"
    MISSING_CONFIG_KEY = "missing_config_key"
    MISSING_CONFIGMAP = "missing_configmap"
    MISSING_SECRET = "missing_secret"
    NETWORK_POLICY_BLOCKING = "network_policy_blocking"
    PVC_UNBOUND = "pvc_unbound"
    IMAGE_PULL_FAILURE = "image_pull_failure"
    IMAGE_PULL_AUTH = "image_pull_auth"
    BAD_COMMAND = "bad_command"
    RESOURCE_QUOTA_EXCEEDED = "resource_quota_exceeded"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    NODE_SELECTOR_MISMATCH = "node_selector_mismatch"
    UNTOLERATED_TAINT = "untolerated_taint"
    INIT_CONTAINER_FAILURE = "init_container_failure"
    RBAC_FORBIDDEN = "rbac_forbidden"
    FILESYSTEM_PERMISSION = "filesystem_permission"
    OTHER = "other"


_READ_ONLY_VERBS = {"get", "describe", "logs", "top", "events", "explain", "api-resources"}
_FLAGS_WITH_VALUE = {"-n", "--namespace", "--context", "--kubeconfig"}
_SHELL_OPERATORS = re.compile(r"[;&|<>`]|\$\(")


def _positional_args(words: list[str]) -> list[str]:
    args = []
    skip_value = False
    for word in words:
        if skip_value:
            skip_value = False
        elif word in _FLAGS_WITH_VALUE:
            skip_value = True
        elif not word.startswith("-"):
            args.append(word)
    return args


def is_read_only_command(command: str) -> bool:
    """True for a single kubectl invocation that cannot change cluster state."""
    if _SHELL_OPERATORS.search(command):
        return False
    words = command.split()
    if not words or words[0] != "kubectl":
        return False
    args = _positional_args(words[1:])
    if not args:
        return False
    verb, rest = args[0], args[1:]
    if verb == "rollout":
        return rest[:1] in (["status"], ["history"])
    if verb == "auth":
        return rest[:1] == ["can-i"]
    return verb in _READ_ONLY_VERBS


class Hypothesis(BaseModel):
    rank: int = Field(ge=1)
    category: RootCause
    title: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1)
    # A hypothesis that cites nothing from the evidence is rejected.
    evidence: list[str] = Field(min_length=1)
    next_command: str = Field(min_length=1, max_length=500)

    @field_validator("title", "explanation", "next_command")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("next_command")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\n" in value:
            raise ValueError("next_command must be a single line")
        return value

    @field_validator("evidence")
    @classmethod
    def _non_blank_evidence(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("evidence must contain at least one non-blank item")
        return cleaned

    @computed_field
    @property
    def command_is_read_only(self) -> bool:
        return is_read_only_command(self.next_command)


class Diagnosis(BaseModel):
    summary: str = Field(min_length=1)
    hypotheses: list[Hypothesis] = Field(min_length=1)

    @property
    def top(self) -> Hypothesis:
        return self.hypotheses[0]


class DiagnosisParseError(Exception):
    pass


# Sent to the API as output_config.format. Kept to the subset structured outputs supports
# (no numeric or length bounds); the Pydantic models above enforce the rest.
DIAGNOSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "category": {"type": "string", "enum": [c.value for c in RootCause]},
                    "title": {"type": "string"},
                    "confidence": {"type": "number"},
                    "explanation": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "next_command": {"type": "string"},
                },
                "required": [
                    "rank",
                    "category",
                    "title",
                    "confidence",
                    "explanation",
                    "evidence",
                    "next_command",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "hypotheses"],
    "additionalProperties": False,
}

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> str:
    text = raw.strip()
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise DiagnosisParseError("response contains no JSON object")
    return text[start : end + 1]


def parse_diagnosis(raw: str) -> Diagnosis:
    """Validate model output. Tolerates code fences and surrounding prose, nothing else."""
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise DiagnosisParseError(f"response is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise DiagnosisParseError("response JSON is not an object")
    try:
        diagnosis = Diagnosis.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise DiagnosisParseError(f"response failed validation: {problems}") from exc

    # Order by the model's rank, break ties by confidence, then renumber so ranks are 1..n.
    ordered = sorted(diagnosis.hypotheses, key=lambda h: (h.rank, -h.confidence))[:MAX_HYPOTHESES]
    for position, hypothesis in enumerate(ordered, start=1):
        hypothesis.rank = position
    diagnosis.hypotheses = ordered
    return diagnosis

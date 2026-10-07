"""Deterministic stand-in for the model: a handful of keyword rules over the prompt.

It exists so the diagnosis path and the eval harness run end to end without an API key
(`LLM_MODE=fake`, `--fake`). Its eval score says how far string matching gets on the
fault library, which is a useful floor to compare the real model against, nothing more.
"""

import json
import re

from app.diagnosis.schema import MAX_HYPOTHESES, RootCause

_RULES: list[tuple[RootCause, str, str]] = [
    (RootCause.RESOURCE_QUOTA_EXCEEDED, r"exceeded quota", "kubectl describe resourcequota"),
    (
        RootCause.IMAGE_PULL_AUTH,
        r"401 Unauthorized|failed to authorize|no basic auth credentials",
        "kubectl get pods -o jsonpath={.items[*].spec.imagePullSecrets}",
    ),
    (RootCause.IMAGE_PULL_FAILURE, r"ErrImagePull|ImagePullBackOff", "kubectl describe pod"),
    (RootCause.MISSING_SECRET, r'secret \\?"[^"\\]+\\?" not found', "kubectl get secrets"),
    (RootCause.MISSING_CONFIGMAP, r'configmap \\?"[^"\\]+\\?" not found', "kubectl get configmaps"),
    (RootCause.MISSING_CONFIG_KEY, r"couldn't find key", "kubectl describe configmap"),
    (
        RootCause.PVC_UNBOUND,
        r"unbound immediate PersistentVolumeClaims|ProvisioningFailed",
        "kubectl describe pvc",
    ),
    (RootCause.UNTOLERATED_TAINT, r"untolerated taint", "kubectl describe nodes"),
    (
        RootCause.NODE_SELECTOR_MISMATCH,
        r"didn't match Pod's node affinity/selector",
        "kubectl get nodes --show-labels",
    ),
    (RootCause.INSUFFICIENT_RESOURCES, r"Insufficient (cpu|memory)", "kubectl describe nodes"),
    (RootCause.INIT_CONTAINER_FAILURE, r'"status": "Init:', "kubectl describe pod"),
    (RootCause.OOM_KILLED, r"OOMKilled", "kubectl top pod"),
    (
        RootCause.RBAC_FORBIDDEN,
        r"is forbidden: User|cannot (get|list|watch) resource",
        "kubectl auth can-i --list",
    ),
    (
        RootCause.FILESYSTEM_PERMISSION,
        r"Read-only file system|Permission denied",
        "kubectl get pod -o yaml",
    ),
    (
        RootCause.BAD_COMMAND,
        r"executable file not found|no such file or directory|StartError",
        "kubectl describe pod",
    ),
    (
        RootCause.NETWORK_POLICY_BLOCKING,
        r"bad address|Temporary failure in name resolution|no such host",
        "kubectl describe networkpolicy",
    ),
    (RootCause.LIVENESS_PROBE_FAILING, r"Liveness probe failed", "kubectl describe pod"),
    (RootCause.READINESS_PROBE_FAILING, r"Readiness probe failed", "kubectl describe pod"),
    (
        RootCause.SERVICE_SELECTOR_MISMATCH,
        r'"selector_matches_target": false',
        "kubectl get endpoints",
    ),
    (
        RootCause.SERVICE_PORT_MISMATCH,
        r'"undeclared_target_ports": \[\s*"',
        "kubectl describe service",
    ),
]


class KeywordBaselineModel:
    name = "keyword-baseline"

    def complete(self, system: str, user: str) -> str:
        namespace = _namespace(user)
        hypotheses = []
        for category, pattern, command in _RULES:
            match = re.search(pattern, user)
            if not match:
                continue
            hypotheses.append(
                {
                    "rank": len(hypotheses) + 1,
                    "category": category.value,
                    "title": category.value.replace("_", " ").capitalize(),
                    "confidence": round(0.6 / (len(hypotheses) + 1), 2),
                    "explanation": f"The evidence matches the keyword rule for {category.value}.",
                    "evidence": [_line_around(user, match.start())],
                    "next_command": f"{command} -n {namespace}",
                }
            )
            if len(hypotheses) == MAX_HYPOTHESES:
                break
        if not hypotheses:
            hypotheses.append(
                {
                    "rank": 1,
                    "category": RootCause.OTHER.value,
                    "title": "No keyword rule matched",
                    "confidence": 0.1,
                    "explanation": "None of the baseline rules matched the evidence.",
                    "evidence": ["no rule matched"],
                    "next_command": f"kubectl get events -n {namespace}",
                }
            )
        return json.dumps(
            {"summary": "Keyword baseline result, not a model answer.", "hypotheses": hypotheses}
        )


def _namespace(prompt: str) -> str:
    match = re.search(r"^Diagnose \w+ ([^/\s]+)/", prompt)
    return match.group(1) if match else "default"


def _line_around(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start : end if end != -1 else len(text)].strip()[:300]

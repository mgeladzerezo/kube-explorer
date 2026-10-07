"""Removes credentials from cluster data before it is shown in the UI or sent to the model.

Two layers:
  * structural rules that know where Kubernetes objects keep sensitive values
    (env entries, Secret data, the last-applied-configuration annotation, CLI flags);
  * text rules for free-form strings such as logs and event messages.

The app has no RBAC permission to read Secrets, so Secret values can only show up
indirectly: echoed in logs, pasted as literal env values, or embedded in arguments.
"""

import re
from collections.abc import Callable
from typing import Any

from app.metrics import REDACTIONS

REDACTED = "[REDACTED]"

_SENSITIVE_WORDS = (
    r"passw(?:or)?d|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|account[_-]?key|credentials?|signature"
)
# Keys that mention a sensitive word but hold a reference, not a value (secretName, tokenPath...).
_REFERENCE_SUFFIXES = ("name", "ref", "file", "path", "mount", "optional", "seconds")

_SENSITIVE_ENV_NAME = re.compile(rf"(?i)({_SENSITIVE_WORDS}|_key$|^key$|auth)")
_SENSITIVE_KEY = re.compile(rf"(?i)^[\w.-]*(?:{_SENSITIVE_WORDS})[\w.-]*$")
_SENSITIVE_FLAG = re.compile(rf"(?i)^--?[\w-]*(?:{_SENSITIVE_WORDS})[\w-]*$")

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
)
_AUTH_HEADER = re.compile(
    r"(?i)\b((?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?)"
    r"((?:bearer|basic|token|digest|negotiate)\s+)?([^\s\"',;]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/:@]*:[^\s/]+@")
_KNOWN_TOKENS = re.compile(
    r"\b(?:A[KS]IA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}"
    r"|sk-ant-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[\w-]{10,}|AIza[\w-]{35})"
)
# No whitespace is allowed before a colon: `volume "db-token" : configmap "x" not found` is
# kubelet prose, not a key/value pair.
_KEY_VALUE = re.compile(
    rf"(?i)([\w.-]*(?:{_SENSITIVE_WORDS})[\w.-]*)([\"']?(?:\s*=|:)\s*[\"']?)([^\s\"',;&{{\[]+)"
)
_FLAG_VALUE = re.compile(rf"(?i)(--[\w-]*(?:{_SENSITIVE_WORDS})[\w-]*\s+)([^\s-]\S*)")

# Patterns specific enough that a match after redaction can only mean a bug.
_HIGH_CONFIDENCE = {
    "private-key": _PRIVATE_KEY,
    "bearer": _BEARER,
    "jwt": _JWT,
    "url-credentials": _URL_CREDENTIALS,
    "known-token": _KNOWN_TOKENS,
}


class RedactionError(Exception):
    pass


def _is_reference_key(key: str) -> bool:
    return key.lower().endswith(_REFERENCE_SUFFIXES)


def _counted(rule: str, replacement: Callable[[re.Match[str]], str] | str):
    def replace(match: re.Match[str]) -> str:
        result = replacement(match) if callable(replacement) else match.expand(replacement)
        if result != match.group(0):
            REDACTIONS.labels(rule).inc()
        return result

    return replace


def _key_value(match: re.Match[str]) -> str:
    key, separator, value = match.groups()
    if _is_reference_key(key) or value.startswith(REDACTED):
        return match.group(0)
    return f"{key}{separator}{REDACTED}"


def _auth_header(match: re.Match[str]) -> str:
    prefix, scheme, _ = match.groups()
    return f"{prefix}{scheme or ''}{REDACTED}"


def redact_text(text: str) -> str:
    text = _PRIVATE_KEY.sub(_counted("private-key", REDACTED), text)
    text = _AUTH_HEADER.sub(_counted("authorization", _auth_header), text)
    text = _BEARER.sub(_counted("bearer", rf"\1{REDACTED}"), text)
    text = _JWT.sub(_counted("jwt", REDACTED), text)
    text = _URL_CREDENTIALS.sub(_counted("url-credentials", rf"\1{REDACTED}@"), text)
    text = _KNOWN_TOKENS.sub(_counted("known-token", REDACTED), text)
    text = _KEY_VALUE.sub(_counted("key-value", _key_value), text)
    return _FLAG_VALUE.sub(_counted("flag", rf"\1{REDACTED}"), text)


def _redact_env_entry(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("value")
    if not isinstance(value, str) or not value:
        return redact_object(entry)
    name = str(entry.get("name", ""))
    # A URL keeps its host and path, which are often the actual clue; only credentials go.
    if _SENSITIVE_ENV_NAME.search(name) and "://" not in value:
        REDACTIONS.labels("env").inc()
        return {**entry, "value": REDACTED}
    return {**entry, "value": redact_text(value)}


def _redact_list(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    hide_next = False
    for item in items:
        if hide_next and isinstance(item, str):
            REDACTIONS.labels("flag").inc()
            result.append(REDACTED)
            hide_next = False
            continue
        # ["--db-password", "hunter2"]: the value is a separate argv element.
        hide_next = isinstance(item, str) and bool(_SENSITIVE_FLAG.match(item))
        result.append(redact_object(item))
    return result


def _redact_mapping(obj: dict[str, Any]) -> dict[str, Any]:
    is_secret = obj.get("kind") == "Secret"
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if key == "kubectl.kubernetes.io/last-applied-configuration":
            continue
        if key == "managedFields":
            continue
        if is_secret and key in ("data", "stringData") and isinstance(value, dict):
            REDACTIONS.labels("secret-data").inc(len(value))
            result[key] = dict.fromkeys(value, REDACTED)
        elif key == "env" and isinstance(value, list):
            result[key] = [
                _redact_env_entry(e) if isinstance(e, dict) else redact_object(e) for e in value
            ]
        elif isinstance(value, str) and _SENSITIVE_KEY.match(key) and not _is_reference_key(key):
            REDACTIONS.labels("sensitive-key").inc()
            result[key] = REDACTED
        else:
            result[key] = redact_object(value)
    return result


def redact_object(obj: Any) -> Any:
    if isinstance(obj, dict):
        return _redact_mapping(obj)
    if isinstance(obj, list):
        return _redact_list(obj)
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def find_leaks(text: str) -> list[str]:
    return [rule for rule, pattern in _HIGH_CONFIDENCE.items() if pattern.search(text)]


def assert_clean(text: str) -> None:
    """Final check on outgoing text. Fails closed instead of sending a suspected credential."""
    leaks = find_leaks(text)
    if leaks:
        raise RedactionError(f"unredacted credential patterns remain: {', '.join(leaks)}")

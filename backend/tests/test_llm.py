from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.diagnosis.llm import FALLBACK_BETA, AnthropicModel, ModelError
from app.diagnosis.schema import DIAGNOSIS_JSON_SCHEMA


def response(stop_reason: str = "end_turn", *blocks) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


class RecordingMessages:
    def __init__(self, result) -> None:
        self._result = result
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def make_model(result, refusal_fallback: bool = True):
    stable, beta = RecordingMessages(result), RecordingMessages(result)
    client = SimpleNamespace(messages=stable, beta=SimpleNamespace(messages=beta))
    model = AnthropicModel(client, "claude-opus-5-5", 16000, "high", refusal_fallback)
    return model, stable, beta


def test_request_asks_for_schema_constrained_output_with_fallback():
    model, stable, beta = make_model(response("end_turn", text('{"ok": true}')))

    assert model.complete("system text", "user text") == '{"ok": true}'

    assert stable.calls == []
    (call,) = beta.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["system"] == "system text"
    assert call["messages"] == [{"role": "user", "content": "user text"}]
    assert call["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": DIAGNOSIS_JSON_SCHEMA},
    }
    assert (call["betas"], call["fallbacks"]) == ([FALLBACK_BETA], "default")
    assert "thinking" not in call and "temperature" not in call


def test_fallback_can_be_switched_off():
    model, stable, beta = make_model(response("end_turn", text("{}")), refusal_fallback=False)
    model.complete("s", "u")

    assert beta.calls == []
    assert "betas" not in stable.calls[0] and "fallbacks" not in stable.calls[0]


def test_text_is_taken_from_the_first_text_block():
    thinking = SimpleNamespace(type="thinking", thinking="")
    model, _, _ = make_model(response("end_turn", thinking, text("answer")))
    assert model.complete("s", "u") == "answer"


@pytest.mark.parametrize(
    ("result", "message", "retryable"),
    [
        (response("refusal"), "declined", False),
        (response("max_tokens", text('{"summary": "tru')), "cut off", False),
        (response("end_turn"), "no text block", False),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com")
            ),
            "could not reach",
            True,
        ),
    ],
)
def test_failures_become_model_errors(result, message, retryable):
    model, _, _ = make_model(result)
    with pytest.raises(ModelError, match=message) as raised:
        model.complete("s", "u")
    assert raised.value.retryable is retryable


def status_error(cls, status: int):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("nope", response=httpx2.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (status_error(anthropic.RateLimitError, 429), True),
        (status_error(anthropic.InternalServerError, 500), True),
        (status_error(anthropic.BadRequestError, 400), False),
        (status_error(anthropic.AuthenticationError, 401), False),
    ],
)
def test_api_status_errors_are_classified(error, retryable):
    model, _, _ = make_model(error)
    with pytest.raises(ModelError) as raised:
        model.complete("s", "u")
    assert raised.value.retryable is retryable

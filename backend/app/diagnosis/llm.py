from typing import Protocol

import anthropic

from app.diagnosis.schema import DIAGNOSIS_JSON_SCHEMA

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelError(Exception):
    """The model could not produce an answer; `retryable` tells the caller whether to try again."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class DiagnosisModel(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str:
        """Return the raw text of the model's answer."""
        ...


class AnthropicModel:
    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        max_tokens: int,
        effort: str,
        refusal_fallback: bool = True,
    ) -> None:
        self._client = client
        self.name = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._refusal_fallback = refusal_fallback

    def complete(self, system: str, user: str) -> str:
        request = {
            "model": self.name,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": DIAGNOSIS_JSON_SCHEMA},
            },
        }
        try:
            if self._refusal_fallback:
                response = self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **request
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.RateLimitError as exc:
            raise ModelError("rate limited by the Anthropic API", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise ModelError(
                f"Anthropic API returned {exc.status_code}: {exc.message}", retryable=retryable
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError("could not reach the Anthropic API", retryable=True) from exc

        if response.stop_reason == "refusal":
            raise ModelError("the model declined to answer this request")
        if response.stop_reason == "max_tokens":
            raise ModelError("the answer was cut off at max_tokens")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ModelError("the response contained no text block")
        return text

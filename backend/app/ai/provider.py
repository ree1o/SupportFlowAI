"""Small typed adapter for the optional local Ollama inference service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from app.config import Settings

MAX_PROMPT_CHARS = 24_000
T = TypeVar("T", bound=BaseModel)


class ProviderUnavailable(Exception):
    """The local model service is not reachable or returned an invalid result."""


@dataclass(frozen=True)
class ModelStatus:
    state: str
    model: str
    digest: str | None = None


class GenerationProvider:
    """Typed generation contract. Fake providers can implement this in tests."""

    def status(self) -> ModelStatus:
        raise NotImplementedError

    def generate_structured(self, prompt: str, response_model: type[T], max_tokens: int) -> T:
        raise NotImplementedError


class OllamaProvider(GenerationProvider):
    def __init__(self, settings: Settings):
        self._settings = settings

    def status(self) -> ModelStatus:
        if not self._settings.ai_enabled:
            return ModelStatus("disabled", self._settings.ai_model)
        try:
            payload = self._request(
                "GET",
                "/api/tags",
                timeout=self._settings.ai_status_timeout_seconds,
            )
            models = payload.get("models")
            if not isinstance(models, list):
                return ModelStatus("unavailable", self._settings.ai_model)
            installed = next(
                (
                    item
                    for item in models
                    if isinstance(item, dict) and item.get("name") == self._settings.ai_model
                ),
                None,
            )
            if installed is None:
                return ModelStatus("model_missing", self._settings.ai_model)
            digest = installed.get("digest")
            if not isinstance(digest, str) or not digest:
                return ModelStatus("unavailable", self._settings.ai_model)
            return ModelStatus("available", self._settings.ai_model, digest)
        except (ProviderUnavailable, TypeError, AttributeError):
            return ModelStatus("unavailable", self._settings.ai_model)

    def generate_structured(self, prompt: str, response_model: type[T], max_tokens: int) -> T:
        if not self._settings.ai_enabled:
            raise ProviderUnavailable("Local AI is disabled")
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError("Prompt is empty or exceeds the configured input bound")
        if max_tokens < 1 or max_tokens > 1024:
            raise ValueError("Output token limit is outside the allowed range")

        payload = {
            "model": self._settings.ai_model,
            "prompt": prompt,
            "stream": False,
            "format": response_model.model_json_schema(),
            "think": False,
            "keep_alive": self._settings.ai_keep_alive,
            "options": {
                "temperature": self._settings.ai_temperature,
                "num_ctx": self._settings.ai_context_tokens,
                "num_predict": max_tokens,
            },
        }
        response = self._request(
            "POST",
            "/api/generate",
            payload,
            timeout=self._settings.ai_timeout_seconds,
        )
        try:
            value = json.loads(response["response"])
            return response_model.model_validate(value)
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise ProviderUnavailable(
                "Local model returned an invalid structured response"
            ) from exc

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: float,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self._settings.ai_ollama_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read(2_000_001))
            if not isinstance(result, dict):
                raise ProviderUnavailable("Local model service returned an invalid response")
            return result
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("Local model service is unavailable") from exc


class MockProvider(GenerationProvider):
    """Deterministic provider for unit/API tests; never contacts a model service."""

    def __init__(self, outputs: dict[str, object] | None = None):
        self.outputs = outputs or {}

    def status(self) -> ModelStatus:
        return ModelStatus("available", "mock")

    def generate_structured(self, prompt: str, response_model: type[T], max_tokens: int) -> T:
        del max_tokens
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError("Prompt is empty or exceeds the configured input bound")
        try:
            return response_model.model_validate(self.outputs)
        except ValidationError as exc:
            raise ProviderUnavailable("Mock output did not satisfy the response contract") from exc

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ConfigDict

from app.ai.provider import (
    MAX_PROMPT_CHARS,
    MockProvider,
    OllamaProvider,
    ProviderUnavailable,
)
from app.config import Settings


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def settings(**values) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:unit-secret@127.0.0.1:1/supportflow_test",
        environment="test",
        **values,
    )


def response(payload: dict) -> MagicMock:
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.read.return_value = json.dumps(payload).encode()
    return fake


def test_ai_is_disabled_by_default_and_has_no_remote_endpoint():
    config = settings()
    assert config.ai_enabled is False
    assert config.ai_model == "qwen3.5:4b-q4_K_M"
    assert config.ai_ollama_url == "http://host.docker.internal:11434"
    assert OllamaProvider(config).status().state == "disabled"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "http://169.254.169.254",
        "http://127.0.0.1:9999",
        "http://host.docker.internal:11434/path",
    ],
)
def test_ollama_endpoint_rejects_non_allowlisted_targets(url: str):
    with pytest.raises(ValueError, match="approved local endpoints"):
        settings(ai_ollama_url=url)


def test_status_distinguishes_unavailable_and_missing_model():
    config = settings(ai_enabled=True)
    provider = OllamaProvider(config)
    with patch.object(provider, "_request", side_effect=ProviderUnavailable("safe")):
        assert provider.status().state == "unavailable"
    with patch.object(provider, "_request", return_value={"models": [{"name": "other"}]}):
        assert provider.status().state == "model_missing"
    with patch.object(provider, "_request", return_value={"models": None}):
        assert provider.status().state == "unavailable"
    with patch.object(
        provider,
        "_request",
        return_value={"models": [{"name": "qwen3.5:4b-q4_K_M", "digest": "sha256:test"}]},
    ):
        status = provider.status()
        assert status.state == "available"
        assert status.digest == "sha256:test"


def test_structured_generation_uses_bounded_local_contract():
    config = settings(ai_enabled=True)
    provider = OllamaProvider(config)
    with patch(
        "app.ai.provider.urlopen", return_value=response({"response": '{"text":"ok"}'})
    ) as call:
        result = provider.generate_structured("safe prompt", Result, 128)
    assert result == Result(text="ok")
    request = call.call_args.args[0]
    sent = json.loads(request.data)
    assert request.full_url == "http://host.docker.internal:11434/api/generate"
    assert sent["model"] == "qwen3.5:4b-q4_K_M"
    assert sent["think"] is False
    assert sent["options"]["num_ctx"] == 4096
    assert sent["options"]["num_predict"] == 128
    assert sent["format"]["additionalProperties"] is False


def test_invalid_model_output_and_oversized_input_fail_safely():
    provider = OllamaProvider(settings(ai_enabled=True))
    with patch("app.ai.provider.urlopen", return_value=response({"response": '{"unexpected":1}'})):
        with pytest.raises(ProviderUnavailable, match="invalid structured response"):
            provider.generate_structured("safe prompt", Result, 128)
    with pytest.raises(ValueError, match="input bound"):
        provider.generate_structured("x" * (MAX_PROMPT_CHARS + 1), Result, 128)
    with pytest.raises(ValueError, match="token limit"):
        provider.generate_structured("safe prompt", Result, 1025)


def test_mock_provider_validates_output_without_network_access():
    assert MockProvider({"text": "draft"}).generate_structured("prompt", Result, 20) == Result(
        text="draft"
    )
    with pytest.raises(ProviderUnavailable):
        MockProvider({"text": 4}).generate_structured("prompt", Result, 20)


def test_tool_provider_uses_local_chat_and_parses_one_typed_call():
    provider = OllamaProvider(settings(ai_enabled=True))
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "get_sla_status",
                        "arguments": {},
                    }
                }
            ],
        }
    }
    tool_schema = [{"type": "function", "function": {"name": "get_sla_status", "parameters": {}}}]
    with patch("app.ai.provider.urlopen", return_value=response(payload)) as call:
        result = provider.generate_with_tools(
            [{"role": "user", "content": "Check SLA status"}], tool_schema, 128
        )
    request = call.call_args.args[0]
    sent = json.loads(request.data)
    assert request.full_url == "http://host.docker.internal:11434/api/chat"
    assert sent["think"] is False
    assert sent["tools"] == tool_schema
    assert result.calls[0].name == "get_sla_status"
    assert result.calls[0].arguments == {}


def test_tool_provider_rejects_multiple_or_malformed_calls():
    provider = OllamaProvider(settings(ai_enabled=True))
    messages = [{"role": "user", "content": "check"}]
    with (
        patch(
            "app.ai.provider.urlopen",
            return_value=response(
                {"message": {"content": "", "tool_calls": [{"function": {}}, {"function": {}}]}}
            ),
        ),
        pytest.raises(ProviderUnavailable, match="invalid tool response"),
    ):
        provider.generate_with_tools(messages, [], 128)

    with (
        patch(
            "app.ai.provider.urlopen",
            return_value=response(
                {"message": {"content": "", "tool_calls": [{"function": {"name": "x"}}]}}
            ),
        ),
        pytest.raises(ProviderUnavailable, match="invalid tool call"),
    ):
        provider.generate_with_tools(messages, [], 128)

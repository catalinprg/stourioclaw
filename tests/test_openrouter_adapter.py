"""Tests for the OpenRouter adapter."""
from __future__ import annotations

import json
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from src.adapters.openrouter import OpenRouterAdapter
from src.adapters.base import LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    return resp


# ── Basic completion ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openrouter_complete_basic():
    """Mock HTTP call, verify request payload shape and response parsing."""
    api_response = {
        "id": "gen-abc123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello from OpenRouter",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }

    adapter = OpenRouterAdapter(
        api_key="test-key",
        model="anthropic/claude-sonnet-4-20250514",
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(200, api_response))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    adapter._client = mock_client

    result = await adapter.complete(
        system_prompt="You are a test assistant.",
        messages=[ChatMessage(role="user", content="Hi")],
        temperature=0.2,
    )

    # Verify response parsing
    assert isinstance(result, LLMResponse)
    assert result.text == "Hello from OpenRouter"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 15

    # Verify request format
    call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert sent_json["model"] == "anthropic/claude-sonnet-4-20250514"
    assert sent_json["temperature"] == 0.2
    assert sent_json["messages"][0] == {"role": "system", "content": "You are a test assistant."}
    assert sent_json["messages"][1] == {"role": "user", "content": "Hi"}


# ── Completion with tool calls ────────────────────────────────────────

@pytest.mark.asyncio
async def test_openrouter_complete_with_tools():
    """Verify tool_calls are parsed from OpenAI-format response."""
    api_response = {
        "id": "gen-tool123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "restart_service",
                                "arguments": json.dumps({"service": "nginx"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }

    adapter = OpenRouterAdapter(api_key="test-key", model="openai/gpt-4o")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(200, api_response))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    adapter._client = mock_client

    tools = [
        ToolDefinition(
            name="restart_service",
            description="Restart a system service",
            parameters={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        )
    ]

    result = await adapter.complete(
        system_prompt="You are an ops agent.",
        messages=[ChatMessage(role="user", content="Restart nginx")],
        tools=tools,
    )

    assert result.has_tool_call
    assert len(result.tool_calls) == 1
    tc = result.first_tool_call
    assert tc["id"] == "call_abc"
    assert tc["name"] == "restart_service"
    assert tc["arguments"] == {"service": "nginx"}

    # Verify tools were sent in request
    call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "tools" in sent_json
    assert sent_json["tools"][0]["type"] == "function"
    assert sent_json["tools"][0]["function"]["name"] == "restart_service"
    assert sent_json["tool_choice"] == "auto"


# ── Fallback routing ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openrouter_fallback_models():
    """Verify fallback routing params are sent when configured."""
    api_response = {
        "id": "gen-fb",
        "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }

    adapter = OpenRouterAdapter(
        api_key="test-key",
        model="anthropic/claude-sonnet-4-20250514",
        fallback_models=["openai/gpt-4o", "google/gemini-2.0-flash"],
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(200, api_response))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    adapter._client = mock_client

    result = await adapter.complete(
        system_prompt="test",
        messages=[ChatMessage(role="user", content="test")],
    )

    assert result.text == "OK"

    call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert sent_json["route"] == "fallback"
    assert sent_json["models"] == [
        "anthropic/claude-sonnet-4-20250514",
        "openai/gpt-4o",
        "google/gemini-2.0-flash",
    ]


# ── Registry tests ────────────────────────────────────────────────────

def test_registry_get_orchestrator_adapter():
    """Registry returns an OpenRouterAdapter for the orchestrator."""
    from src.adapters.registry import get_orchestrator_adapter, reset_adapters
    reset_adapters()

    with patch("src.adapters.registry.settings") as mock_settings:
        mock_settings.openrouter_api_key = "or-test"
        mock_settings.orchestrator_model = "openai/gpt-4o-mini"
        mock_settings.openrouter_fallback_models = []
        mock_settings.openrouter_fallback_enabled = False

        adapter = get_orchestrator_adapter()
        assert isinstance(adapter, OpenRouterAdapter)
        assert adapter.model == "openai/gpt-4o-mini"

    reset_adapters()


def test_registry_get_agent_adapter():
    """Registry returns cached singletons per model."""
    from src.adapters.registry import get_agent_adapter, reset_adapters
    reset_adapters()

    with patch("src.adapters.registry.settings") as mock_settings:
        mock_settings.openrouter_api_key = "or-test"
        mock_settings.openrouter_fallback_models = ["openai/gpt-4o"]
        mock_settings.openrouter_fallback_enabled = True

        a1 = get_agent_adapter("anthropic/claude-sonnet-4-20250514")
        a2 = get_agent_adapter("anthropic/claude-sonnet-4-20250514")
        a3 = get_agent_adapter("openai/gpt-4o")

        assert a1 is a2  # same instance (cached)
        assert a1 is not a3  # different model = different instance
        assert isinstance(a1, OpenRouterAdapter)

    reset_adapters()


# ── HTTP error handling ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openrouter_http_error_raises():
    """Non-200 responses raise an exception."""
    error_body = {"error": {"message": "Rate limit exceeded", "code": 429}}

    adapter = OpenRouterAdapter(api_key="test-key", model="test/model")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_response(429, error_body))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    adapter._client = mock_client

    with pytest.raises(Exception, match="OpenRouter API error 429"):
        await adapter.complete(
            system_prompt="test",
            messages=[ChatMessage(role="user", content="test")],
        )

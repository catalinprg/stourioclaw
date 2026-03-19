import pytest
import json
from unittest.mock import AsyncMock
from src.adapters.cache import build_cache_key, CachedLLMAdapter
from src.adapters.base import LLMResponse
from src.models.schemas import TokenUsage, ChatMessage


def test_build_cache_key_deterministic():
    messages = [ChatMessage(role="user", content="hello")]
    key1 = build_cache_key("openai", "gpt-4o", "You are a bot.", messages, None)
    key2 = build_cache_key("openai", "gpt-4o", "You are a bot.", messages, None)
    assert key1 == key2
    assert key1.startswith("stourio:llm_cache:")


def test_build_cache_key_different_prompts():
    messages = [ChatMessage(role="user", content="hello")]
    key1 = build_cache_key("openai", "gpt-4o", "You are a helpful assistant.", messages, None)
    key2 = build_cache_key("openai", "gpt-4o", "You are a dangerous assistant.", messages, None)
    assert key1 != key2


@pytest.mark.asyncio
async def test_cached_adapter_miss(mock_llm_adapter, mock_redis):
    mock_redis.get.return_value = None
    cached = CachedLLMAdapter(adapter=mock_llm_adapter, redis=mock_redis, ttl=300)

    messages = [ChatMessage(role="user", content="test query")]
    result = await cached.complete("system prompt", messages)

    # Adapter was called since cache missed
    mock_llm_adapter.complete.assert_called_once()
    # Cache was written
    mock_redis.setex.assert_called_once()
    assert result.text == "Test response"


@pytest.mark.asyncio
async def test_cached_adapter_ttl_zero(mock_llm_adapter, mock_redis):
    cached = CachedLLMAdapter(adapter=mock_llm_adapter, redis=mock_redis, ttl=0)

    messages = [ChatMessage(role="user", content="test query")]
    result = await cached.complete("system prompt", messages)

    # Adapter was called directly
    mock_llm_adapter.complete.assert_called_once()
    # Redis was never touched
    mock_redis.get.assert_not_called()
    mock_redis.setex.assert_not_called()

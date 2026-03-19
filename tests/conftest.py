import pytest
from unittest.mock import AsyncMock, MagicMock
from src.adapters.base import LLMResponse
from src.models.schemas import TokenUsage


@pytest.fixture
def mock_llm_adapter():
    adapter = AsyncMock()
    adapter.provider_name = "test"
    adapter.model = "test-model"
    adapter.complete.return_value = LLMResponse(text="Test response", tool_calls=None, raw={}, usage=TokenUsage())
    return adapter


@pytest.fixture
def mock_llm_with_tool_call():
    adapter = AsyncMock()
    adapter.provider_name = "test"
    adapter.complete.side_effect = [
        LLMResponse(text=None, tool_calls=[{"id": "call_1", "name": "search_knowledge", "arguments": {"query": "redis"}}], raw={}, usage=TokenUsage()),
        LLMResponse(text="Issue resolved.", tool_calls=None, raw={}, usage=TokenUsage()),
    ]
    return adapter


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex.return_value = True
    redis.delete.return_value = True
    return redis

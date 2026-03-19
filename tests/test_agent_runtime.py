"""Tests for the DB-backed agent runtime."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.base import LLMResponse
from src.agents.runtime import execute_agent, _resolve_tools, default_tool_executor
from src.models.schemas import TokenUsage, ToolDefinition
from src.persistence.database import AgentModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(**overrides) -> AgentModel:
    defaults = dict(
        id="01AGENT",
        name="diagnose_repair",
        display_name="Diagnose & Repair",
        description="SRE diagnosis agent",
        system_prompt="You are a diagnostic agent.",
        model="anthropic/claude-sonnet-4-20250514",
        tools=["get_system_metrics", "get_recent_logs"],
        max_steps=8,
        max_concurrent=3,
        is_active=True,
        is_system=True,
    )
    defaults.update(overrides)
    return AgentModel(**defaults)


def _simple_response(text="Issue resolved."):
    return LLMResponse(text=text, tool_calls=None, raw={}, usage=TokenUsage())


def _tool_call_response(name="get_system_metrics", args=None):
    return LLMResponse(
        text=None,
        tool_calls=[{"id": "call_1", "name": name, "arguments": args or {"component": "cpu"}}],
        raw={},
        usage=TokenUsage(),
    )


def _mock_redis(*, lock_token="token-123", fencing_valid=True, killed=False):
    """Build a mock that behaves like the redis_store module (async functions)."""
    m = MagicMock()
    m.acquire_lock_with_token = AsyncMock(return_value=lock_token)
    m.validate_fencing_token = AsyncMock(return_value=fencing_valid)
    m.extend_lock = AsyncMock()
    m.release_lock = AsyncMock()
    m.is_killed = AsyncMock(return_value=killed)
    return m


def _mock_audit():
    m = MagicMock()
    m.log = AsyncMock()
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_agent_loads_from_db():
    """Agent runtime loads template from DB registry instead of hardcoded dict."""
    agent = _make_agent()
    mock_session = AsyncMock()

    with (
        patch("src.agents.runtime.AgentRegistry") as MockRegistry,
        patch("src.agents.runtime.get_agent_adapter") as mock_get_adapter,
        patch("src.agents.runtime.redis_store", _mock_redis()),
        patch("src.agents.runtime.audit", _mock_audit()),
        patch("src.agents.runtime.get_history", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.runtime._resolve_tools", return_value=[]),
        patch("src.tools.python.knowledge_search._retriever", None),
    ):
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = agent
        MockRegistry.return_value = registry_instance

        mock_adapter = AsyncMock()
        mock_adapter.complete.return_value = _simple_response("Fixed the CPU issue.")
        mock_get_adapter.return_value = mock_adapter

        result = await execute_agent(
            agent_name="diagnose_repair",
            objective="CPU spike on web-01",
            context="Alert: CPU > 95% for 5 minutes",
            session=mock_session,
        )

    registry_instance.get_by_name.assert_awaited_once_with("diagnose_repair")
    mock_get_adapter.assert_called_once_with("anthropic/claude-sonnet-4-20250514")
    assert result.status.value == "completed"
    assert result.result == "Fixed the CPU issue."
    assert result.agent_type == "diagnose_repair"


@pytest.mark.asyncio
async def test_execute_agent_unknown_agent_raises():
    """Unknown agent name raises ValueError."""
    mock_session = AsyncMock()

    with patch("src.agents.runtime.AgentRegistry") as MockRegistry:
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = None
        MockRegistry.return_value = registry_instance

        with pytest.raises(ValueError, match="Unknown agent: nonexistent"):
            await execute_agent(
                agent_name="nonexistent",
                objective="test",
                context="test",
                session=mock_session,
            )


@pytest.mark.asyncio
async def test_execute_agent_with_tool_call():
    """Agent runtime handles tool calls from LLM response."""
    agent = _make_agent()
    mock_session = AsyncMock()

    async def mock_tool_exec(name, args):
        return '{"cpu": 42.5}'

    with (
        patch("src.agents.runtime.AgentRegistry") as MockRegistry,
        patch("src.agents.runtime.get_agent_adapter") as mock_get_adapter,
        patch("src.agents.runtime.redis_store", _mock_redis(lock_token="token-456")),
        patch("src.agents.runtime.audit", _mock_audit()),
        patch("src.agents.runtime.get_history", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.runtime._resolve_tools", return_value=[]),
        patch("src.tools.python.knowledge_search._retriever", None),
    ):
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = agent
        MockRegistry.return_value = registry_instance

        mock_adapter = AsyncMock()
        mock_adapter.complete.side_effect = [
            _tool_call_response(),
            _simple_response("CPU is normal at 42.5%."),
        ]
        mock_get_adapter.return_value = mock_adapter

        result = await execute_agent(
            agent_name="diagnose_repair",
            objective="Check CPU",
            context="Routine check",
            session=mock_session,
            tool_executor=mock_tool_exec,
        )

    assert result.status.value == "completed"
    assert len(result.steps) == 2
    assert result.steps[0]["type"] == "tool_call"
    assert result.steps[0]["tool"] == "get_system_metrics"
    assert result.steps[1]["type"] == "response"


@pytest.mark.asyncio
async def test_execute_agent_lock_failure():
    """Lock acquisition failure returns FAILED execution without calling LLM."""
    agent = _make_agent()
    mock_session = AsyncMock()

    with (
        patch("src.agents.runtime.AgentRegistry") as MockRegistry,
        patch("src.agents.runtime.redis_store", _mock_redis(lock_token=None)),
        patch("src.agents.runtime._resolve_tools", return_value=[]),
    ):
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = agent
        MockRegistry.return_value = registry_instance

        result = await execute_agent(
            agent_name="diagnose_repair",
            objective="test",
            context="test",
            session=mock_session,
        )

    assert result.status.value == "failed"
    assert "Lock acquisition failed" in result.result


@pytest.mark.asyncio
async def test_execute_agent_kill_switch():
    """Kill switch halts the agent."""
    agent = _make_agent()
    mock_session = AsyncMock()

    with (
        patch("src.agents.runtime.AgentRegistry") as MockRegistry,
        patch("src.agents.runtime.get_agent_adapter") as mock_get_adapter,
        patch("src.agents.runtime.redis_store", _mock_redis(lock_token="token-789", killed=True)),
        patch("src.agents.runtime.audit", _mock_audit()),
        patch("src.agents.runtime.get_history", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.runtime._resolve_tools", return_value=[]),
        patch("src.tools.python.knowledge_search._retriever", None),
    ):
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = agent
        MockRegistry.return_value = registry_instance

        mock_get_adapter.return_value = AsyncMock()

        result = await execute_agent(
            agent_name="diagnose_repair",
            objective="test",
            context="test",
            session=mock_session,
        )

    assert result.status.value == "halted"
    assert "kill switch" in result.result.lower()


def test_resolve_tools_from_plugin_registry():
    """_resolve_tools converts tool name strings to ToolDefinition objects."""
    agent = _make_agent(tools=["get_system_metrics", "nonexistent_tool"])

    mock_tool = MagicMock()
    mock_tool.name = "get_system_metrics"
    mock_tool.description = "Get system metrics"
    mock_tool.parameters = {"type": "object", "properties": {}}

    mock_registry = MagicMock()
    mock_registry.get.side_effect = lambda name: mock_tool if name == "get_system_metrics" else None

    with patch("src.agents.runtime.get_registry", return_value=mock_registry):
        tools = _resolve_tools(agent)

    assert len(tools) == 1
    assert tools[0].name == "get_system_metrics"


@pytest.mark.asyncio
async def test_no_provider_failover_logic():
    """Runtime does NOT contain provider failover — OpenRouter handles it."""
    agent = _make_agent()
    mock_session = AsyncMock()

    with (
        patch("src.agents.runtime.AgentRegistry") as MockRegistry,
        patch("src.agents.runtime.get_agent_adapter") as mock_get_adapter,
        patch("src.agents.runtime.redis_store", _mock_redis(lock_token="token-abc")),
        patch("src.agents.runtime.audit", _mock_audit()),
        patch("src.agents.runtime.get_history", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.runtime._resolve_tools", return_value=[]),
        patch("src.tools.python.knowledge_search._retriever", None),
    ):
        registry_instance = AsyncMock()
        registry_instance.get_by_name.return_value = agent
        MockRegistry.return_value = registry_instance

        mock_adapter = AsyncMock()
        mock_adapter.complete.side_effect = Exception("LLM provider down")
        mock_get_adapter.return_value = mock_adapter

        result = await execute_agent(
            agent_name="diagnose_repair",
            objective="test",
            context="test",
            session=mock_session,
        )

    assert result.status.value == "failed"
    assert "LLM provider down" in result.result
    # get_agent_adapter called exactly once — no failover retry
    mock_get_adapter.assert_called_once()


@pytest.mark.asyncio
async def test_tool_executor_passes_agent_name():
    """default_tool_executor should pass agent_name to registry.execute."""
    from src.agents.runtime import default_tool_executor

    with patch("src.agents.runtime.get_registry") as mock_get_registry:
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value={"result": "ok"})
        mock_get_registry.return_value = mock_registry

        await default_tool_executor("web_search", {"query": "test"}, agent_name="analyst")

        mock_registry.execute.assert_called_once_with("web_search", {"query": "test"}, agent_name="analyst")

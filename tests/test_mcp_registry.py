"""Tests for the in-process MCP Tool Registry."""

import pytest

from src.mcp.base import Tool
from src.mcp.registry import ToolRegistry, register_tool


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _echo_fn(arguments: dict) -> dict:
    return {"echo": arguments}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_register_and_list_tool():
    """Register a tool via decorator; verify it appears in list_tools."""
    registry = ToolRegistry()

    @register_tool(registry, "greet", "Says hello", {"type": "object"})
    async def greet(arguments: dict) -> dict:
        return {"msg": f"Hello {arguments.get('name')}"}

    assert registry.has("greet")
    tools = registry.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "greet"


@pytest.mark.asyncio
async def test_execute_registered_tool():
    """Execute a registered tool and verify the result."""
    registry = ToolRegistry()
    tool = Tool(name="echo", description="Echo args", execute_fn=_echo_fn)
    registry.register(tool)

    result = await registry.execute("echo", {"key": "value"})
    assert result == {"echo": {"key": "value"}}


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises():
    """Executing an unregistered tool must raise ValueError."""
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="not registered"):
        await registry.execute("nonexistent", {})


def test_register_invalid_name_raises():
    """Tool names with spaces or special chars must be rejected."""
    registry = ToolRegistry()
    tool = Tool(name="bad name!", description="invalid")
    with pytest.raises(ValueError, match="Invalid tool name"):
        registry.register(tool)


def test_get_unknown_tool_raises():
    """get() on missing tool must raise ValueError."""
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="not registered"):
        registry.get("missing")


def test_to_tool_definitions():
    """to_tool_definitions() returns LLM-compatible format."""
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="search",
            description="Search things",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            execute_fn=_echo_fn,
        )
    )
    defs = registry.to_tool_definitions()
    assert len(defs) == 1
    assert defs[0]["type"] == "function"
    assert defs[0]["function"]["name"] == "search"
    assert defs[0]["function"]["parameters"]["properties"]["q"]["type"] == "string"


@pytest.mark.asyncio
async def test_execute_tool_without_execute_fn_raises():
    """Tool with no execute_fn must raise ValueError on execute."""
    registry = ToolRegistry()
    registry.register(Tool(name="noop", description="No-op"))
    with pytest.raises(ValueError, match="no execute_fn"):
        await registry.execute("noop", {})

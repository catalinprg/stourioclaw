import pytest
from unittest.mock import AsyncMock
from src.plugins.registry import ToolRegistry
from src.plugins.base import BaseTool
from src.plugins.yaml_tool import YamlTool


class DummyTool(BaseTool):
    name = "dummy_tool"
    description = "A test tool"
    parameters = {"type": "object", "properties": {}}
    execution_mode = "local"

    async def execute(self, arguments: dict) -> dict:
        return {"result": "dummy_output"}


def test_registry_register_and_get():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    retrieved = registry.get("dummy_tool")
    assert retrieved is tool


@pytest.mark.asyncio
async def test_registry_execute():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    result = await registry.execute("dummy_tool", {})
    assert result == {"result": "dummy_output"}


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="not registered"):
        await registry.execute("nonexistent_tool", {})


def test_registry_list_tools():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    definitions = registry.list_tools()
    assert len(definitions) == 1
    assert definitions[0]["name"] == "dummy_tool"
    assert "description" in definitions[0]
    assert "parameters" in definitions[0]


def test_yaml_tool_parsing():
    definition = {
        "name": "yaml_test_tool",
        "description": "A YAML-defined tool",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        "execution_mode": "local",
        "request": {"method": "GET", "url": "https://example.com/api"},
        "response": {},
    }
    tool = YamlTool(definition)
    assert tool.name == "yaml_test_tool"
    assert tool.description == "A YAML-defined tool"
    assert tool.execution_mode == "local"


def test_base_tool_interface():
    tool = DummyTool()
    assert hasattr(tool, "execute")
    assert hasattr(tool, "validate")
    assert hasattr(tool, "health_check")
    assert hasattr(tool, "to_tool_definition")
    definition = tool.to_tool_definition()
    assert definition["name"] == "dummy_tool"

"""Tests for dynamic orchestrator routing tools (Task 8)."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from src.orchestrator.core import build_routing_tools


def _make_agent(name: str, description: str) -> MagicMock:
    agent = MagicMock()
    agent.name = name
    agent.description = description
    return agent


class TestBuildRoutingTools:
    """Tests for the build_routing_tools function."""

    def test_routing_tools_built_dynamically(self):
        """Routing tools enum includes provided agents, excludes non-routable."""
        mock_agents = [
            _make_agent("assistant", "General tasks"),
            _make_agent("analyst", "Data analysis"),
            _make_agent("code_writer", "Code generation"),
            _make_agent("intel", "Deep reasoning"),
        ]
        tools = build_routing_tools(mock_agents)
        route_tool = next(t for t in tools if t.name == "route_to_agent")
        agent_enum = route_tool.parameters["properties"]["agent_type"]["enum"]
        assert "assistant" in agent_enum
        assert "analyst" in agent_enum
        assert "code_writer" in agent_enum
        assert "intel" in agent_enum
        # Non-routable agents should not appear (they aren't in the input list)
        assert "cybersecurity" not in agent_enum
        assert "code_reviewer" not in agent_enum

    def test_routing_tools_no_automation(self):
        """route_to_automation tool is removed."""
        tools = build_routing_tools([_make_agent("assistant", "General")])
        tool_names = [t.name for t in tools]
        assert "route_to_automation" not in tool_names

    def test_routing_tools_keeps_required_tools(self):
        """Core routing tools are always present."""
        tools = build_routing_tools([_make_agent("assistant", "General")])
        tool_names = [t.name for t in tools]
        assert "route_to_agent" in tool_names
        assert "respond_directly" in tool_names
        assert "request_more_info" in tool_names
        assert "route_to_chain" in tool_names

    def test_routing_tools_empty_agents(self):
        """When no agents are provided, route_to_agent still exists with empty enum."""
        tools = build_routing_tools([])
        route_tool = next(t for t in tools if t.name == "route_to_agent")
        agent_enum = route_tool.parameters["properties"]["agent_type"]["enum"]
        assert agent_enum == []

    def test_agent_descriptions_in_route_tool(self):
        """Agent descriptions are included so the LLM can make informed decisions."""
        agents = [
            _make_agent("assistant", "General tasks and Q&A"),
            _make_agent("analyst", "Data analysis and reporting"),
        ]
        tools = build_routing_tools(agents)
        route_tool = next(t for t in tools if t.name == "route_to_agent")
        desc = route_tool.parameters["properties"]["agent_type"]["description"]
        assert "assistant" in desc
        assert "General tasks" in desc
        assert "analyst" in desc
        assert "Data analysis" in desc

    def test_single_agent(self):
        """Works with a single agent."""
        tools = build_routing_tools([_make_agent("solo", "Only agent")])
        route_tool = next(t for t in tools if t.name == "route_to_agent")
        assert route_tool.parameters["properties"]["agent_type"]["enum"] == ["solo"]

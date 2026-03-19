"""Tests for MCP Server and Router."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.mcp.base import Tool
from src.mcp.registry import ToolRegistry
from src.mcp.server import create_mcp_server, _build_input_schema


# ---------------------------------------------------------------------------
# Unit: server creation
# ---------------------------------------------------------------------------

class TestMcpServerCreation:
    def test_mcp_server_has_name(self):
        server = create_mcp_server()
        assert server.name == "stourioclaw"

    def test_mcp_server_exists(self):
        from src.mcp.server import mcp_server
        assert mcp_server is not None
        assert mcp_server.name == "stourioclaw"


# ---------------------------------------------------------------------------
# Unit: input schema builder
# ---------------------------------------------------------------------------

class TestInputSchemaBuilder:
    def test_empty_params(self):
        schema = _build_input_schema({})
        assert schema == {"type": "object", "properties": {}}

    def test_passthrough_valid_schema(self):
        raw = {"type": "object", "properties": {"q": {"type": "string"}}}
        assert _build_input_schema(raw) == raw

    def test_legacy_params_wrapped(self):
        raw = {"query": "search term"}
        schema = _build_input_schema(raw)
        assert schema["type"] == "object"
        assert "query" in schema["properties"]


# ---------------------------------------------------------------------------
# Integration: /mcp/tools REST endpoint
# ---------------------------------------------------------------------------

class TestMcpToolsEndpoint:
    @pytest.fixture(autouse=True)
    def _setup_app(self):
        """Create a test app with mcp_router and a test tool registered."""
        from src.mcp.router import mcp_router
        from src.mcp.registry import tool_registry

        # Register a test tool
        test_tool = Tool(
            name="test_ping",
            description="Returns pong",
            parameters={"type": "object", "properties": {}},
            execute_fn=AsyncMock(return_value={"result": "pong"}),
        )
        tool_registry.register(test_tool)

        app = FastAPI()
        app.include_router(mcp_router)
        self.client = TestClient(app)

        yield

        # Cleanup: remove test tool
        tool_registry._tools.pop("test_ping", None)

    def test_mcp_tools_endpoint_returns_list(self):
        resp = self.client.get("/mcp/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "count" in data
        assert isinstance(data["tools"], list)

    def test_mcp_tools_endpoint_contains_registered_tool(self):
        resp = self.client.get("/mcp/tools")
        data = resp.json()
        names = [t["name"] for t in data["tools"]]
        assert "test_ping" in names

    def test_mcp_tools_endpoint_tool_structure(self):
        resp = self.client.get("/mcp/tools")
        data = resp.json()
        tool = next(t for t in data["tools"] if t["name"] == "test_ping")
        assert tool["description"] == "Returns pong"
        assert "parameters" in tool

"""Tests for MCP client pool."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os


@pytest.mark.asyncio
async def test_mcp_client_pool_not_connected():
    from src.mcp.client import McpClientPool
    pool = McpClientPool()
    assert pool.is_connected("notion") is False


@pytest.mark.asyncio
async def test_mcp_client_pool_get_tools_unknown_server():
    from src.mcp.client import McpClientPool
    pool = McpClientPool()
    tools = pool.get_tools("nonexistent")
    assert tools == []


@pytest.mark.asyncio
async def test_mcp_client_pool_resolve_auth():
    from src.mcp.client import McpClientPool
    pool = McpClientPool()
    os.environ["TEST_MCP_TOKEN"] = "secret123"
    try:
        result = pool._resolve_auth("TEST_MCP_TOKEN")
        assert result == "secret123"
    finally:
        del os.environ["TEST_MCP_TOKEN"]


@pytest.mark.asyncio
async def test_mcp_client_pool_resolve_auth_missing():
    from src.mcp.client import McpClientPool
    pool = McpClientPool()
    result = pool._resolve_auth("NONEXISTENT_VAR")
    assert result is None


@pytest.mark.asyncio
async def test_get_all_tools_for_agent():
    from src.mcp.client import McpClientPool
    from src.models.schemas import ToolDefinition

    pool = McpClientPool()
    pool._tools["notion"] = [
        ToolDefinition(name="notion__search", description="Search Notion", parameters={}),
    ]
    pool._tools["github"] = [
        ToolDefinition(name="github__list_prs", description="List PRs", parameters={}),
    ]

    tools = pool.get_all_tools_for_agent(["notion", "github"])
    assert len(tools) == 2

    tools_partial = pool.get_all_tools_for_agent(["notion"])
    assert len(tools_partial) == 1


@pytest.mark.asyncio
async def test_disconnect():
    from src.mcp.client import McpClientPool
    pool = McpClientPool()
    pool._connections["test"] = {"session": MagicMock()}
    pool._tools["test"] = []
    pool._server_configs["test"] = {}

    await pool.disconnect("test")
    assert "test" not in pool._connections
    assert "test" not in pool._tools

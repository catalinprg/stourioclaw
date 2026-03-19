"""MCP Server via official Python SDK.

Creates a real MCP server that Claude Code connects to via SSE transport.
Tools are sourced from our in-process ToolRegistry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import Tool as McpTool, TextContent

from src.mcp.registry import tool_registry

logger = logging.getLogger("stourio.mcp.server")


def _build_input_schema(params: dict[str, Any]) -> dict[str, Any]:
    """Convert our tool parameters dict to a JSON Schema object.

    If the tool already provides a valid JSON Schema with 'type': 'object',
    pass it through. Otherwise wrap it as an object schema.
    """
    if not params:
        return {"type": "object", "properties": {}}
    if params.get("type") == "object" and "properties" in params:
        return params
    # Legacy format: treat keys as simple string properties
    return {
        "type": "object",
        "properties": {
            k: v if isinstance(v, dict) else {"type": "string", "description": str(v)}
            for k, v in params.items()
        },
    }


def create_mcp_server() -> Server:
    """Build and return a configured MCP Server backed by our ToolRegistry."""
    server = Server("stourioclaw")

    @server.list_tools()
    async def list_tools() -> list[McpTool]:
        tools: list[McpTool] = []
        for tool in tool_registry.list_tools():
            tools.append(
                McpTool(
                    name=tool.name,
                    description=tool.description or "",
                    inputSchema=_build_input_schema(tool.parameters),
                )
            )
        logger.debug("MCP list_tools: returning %d tools", len(tools))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        logger.info("MCP call_tool: %s", name)
        try:
            result = await tool_registry.execute(name, arguments, agent_name="mcp-client")
            # Serialize result to text
            if isinstance(result, str):
                text = result
            elif isinstance(result, dict):
                text = json.dumps(result, indent=2, default=str)
            else:
                text = str(result)
            return [TextContent(type="text", text=text)]
        except Exception as exc:
            logger.error("MCP call_tool error for '%s': %s", name, exc)
            return [TextContent(type="text", text=f"Error: {exc}")]

    return server


# Singleton for use by the router
mcp_server = create_mcp_server()

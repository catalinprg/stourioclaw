"""MCP client pool — manages persistent connections to external MCP servers.

Agents use this to discover and call tools on external MCP servers
(Notion, GitHub, Slack, etc.). Connections are held open across tool calls.
Auth is resolved from environment variables at connection time.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.models.schemas import ToolDefinition

logger = logging.getLogger("stourio.mcp.client")


class McpClientPool:
    """Manages persistent connections to external MCP servers.

    Connections are lazy-initialized and held open across tool calls.
    Tool definitions are cached after initial discovery.
    """

    def __init__(self):
        self._connections: dict[str, dict] = {}  # server_name -> {session, read, write, ...}
        self._tools: dict[str, list[ToolDefinition]] = {}  # server_name -> tool defs
        self._server_configs: dict[str, dict] = {}  # server_name -> config

    def _resolve_auth(self, config: dict) -> str | None:
        """Resolve auth token from env var or encrypted DB storage."""
        # Try env var first
        env_var = config.get("auth_env_var")
        if env_var:
            token = os.environ.get(env_var)
            if token:
                return token

        # Try encrypted token from DB
        encrypted = config.get("auth_token_encrypted")
        if encrypted:
            from src.mcp.crypto import decrypt_token
            return decrypt_token(encrypted)

        return None

    def is_connected(self, server_name: str) -> bool:
        return server_name in self._connections

    def get_tools(self, server_name: str) -> list[ToolDefinition]:
        """Get cached tool definitions for a server."""
        return self._tools.get(server_name, [])

    def get_all_tools_for_agent(self, server_names: list[str]) -> list[ToolDefinition]:
        """Get all tool definitions from multiple MCP servers."""
        tools = []
        for name in server_names:
            tools.extend(self.get_tools(name))
        return tools

    async def connect(self, server_name: str, config: dict) -> bool:
        """Connect to an MCP server and discover its tools.

        For SSE transport: opens a persistent SSE connection and MCP session.
        For stdio transport: launches the subprocess and holds the session.
        """
        if server_name in self._connections:
            logger.info("Already connected to '%s'", server_name)
            return True

        transport = config.get("transport")
        self._server_configs[server_name] = config

        try:
            if transport == "sse":
                endpoint = config.get("endpoint_url")
                if not endpoint:
                    logger.error("MCP server '%s' missing endpoint_url", server_name)
                    return False

                logger.info("Connecting to MCP server '%s' via SSE: %s", server_name, endpoint)

                from mcp.client.sse import sse_client
                from mcp import ClientSession

                # Open persistent connection — store context managers
                sse_cm = sse_client(endpoint)
                read_stream, write_stream = await sse_cm.__aenter__()

                session_cm = ClientSession(read_stream, write_stream)
                session = await session_cm.__aenter__()
                await session.initialize()

                # Discover tools
                tools_result = await session.list_tools()
                tool_defs = []
                for tool in tools_result.tools:
                    td = ToolDefinition(
                        name=f"{server_name}__{tool.name}",
                        description=f"[{server_name}] {tool.description or ''}",
                        parameters=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                    )
                    tool_defs.append(td)

                self._tools[server_name] = tool_defs
                self._connections[server_name] = {
                    "transport": "sse",
                    "session": session,
                    "session_cm": session_cm,
                    "sse_cm": sse_cm,
                }

                logger.info("Connected to '%s': %d tools discovered", server_name, len(tool_defs))
                return True

            elif transport == "stdio":
                endpoint_cmd = config.get("endpoint_command")
                if not endpoint_cmd:
                    logger.error("MCP server '%s' missing endpoint_command", server_name)
                    return False

                from src.config import settings
                allowed = getattr(settings, 'mcp_stdio_allowed_commands', [])
                if endpoint_cmd not in allowed:
                    logger.error("MCP stdio command '%s' not in allowlist", endpoint_cmd)
                    return False

                logger.info("Connecting to MCP server '%s' via stdio: %s", server_name, endpoint_cmd)

                from mcp.client.stdio import stdio_client, StdioServerParameters
                from mcp import ClientSession

                # Parse command into executable + args
                parts = endpoint_cmd.split()
                server_params = StdioServerParameters(command=parts[0], args=parts[1:] if len(parts) > 1 else [])

                stdio_cm = stdio_client(server_params)
                read_stream, write_stream = await stdio_cm.__aenter__()

                session_cm = ClientSession(read_stream, write_stream)
                session = await session_cm.__aenter__()
                await session.initialize()

                # Discover tools
                tools_result = await session.list_tools()
                tool_defs = []
                for tool in tools_result.tools:
                    td = ToolDefinition(
                        name=f"{server_name}__{tool.name}",
                        description=f"[{server_name}] {tool.description or ''}",
                        parameters=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                    )
                    tool_defs.append(td)

                self._tools[server_name] = tool_defs
                self._connections[server_name] = {
                    "transport": "stdio",
                    "session": session,
                    "session_cm": session_cm,
                    "stdio_cm": stdio_cm,
                }

                logger.info("Connected to '%s': %d tools discovered", server_name, len(tool_defs))
                return True

            else:
                logger.error("Unknown transport '%s' for MCP server '%s'", transport, server_name)
                return False

        except Exception as e:
            logger.error("Failed to connect to MCP server '%s': %s", server_name, e)
            return False

    async def execute_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        """Execute a tool on a remote MCP server using the persistent session."""
        conn = self._connections.get(server_name)
        if not conn:
            return {"error": f"MCP server '{server_name}' not connected"}

        session = conn.get("session")
        if not session:
            return {"error": f"MCP server '{server_name}' has no active session"}

        try:
            result = await session.call_tool(tool_name, arguments)
            if result.content:
                texts = [c.text for c in result.content if hasattr(c, 'text')]
                return {"result": "\n".join(texts)}
            return {"result": "Tool completed with no output"}
        except Exception as e:
            logger.error("MCP tool execution failed: %s/%s: %s", server_name, tool_name, e)
            return {"error": f"MCP tool execution failed: {str(e)}"}

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from an MCP server, closing all sessions."""
        conn = self._connections.pop(server_name, None)
        if conn:
            # Close in reverse order: session first, then transport
            try:
                session_cm = conn.get("session_cm")
                if session_cm:
                    await session_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("Error closing MCP session for '%s': %s", server_name, e)

            try:
                transport_cm = conn.get("sse_cm") or conn.get("stdio_cm")
                if transport_cm:
                    await transport_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("Error closing MCP transport for '%s': %s", server_name, e)

        self._tools.pop(server_name, None)
        self._server_configs.pop(server_name, None)
        logger.info("Disconnected from MCP server: %s", server_name)

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(self._connections.keys()):
            await self.disconnect(name)


# Global singleton
_pool: McpClientPool | None = None


def get_mcp_client_pool() -> McpClientPool:
    global _pool
    if _pool is None:
        _pool = McpClientPool()
    return _pool

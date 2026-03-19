"""In-process MCP Tool Registry.

Replaces the old gateway-based dispatch with direct function calls.
Security interceptor integration (Task 23) will be added later.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from src.mcp.base import Tool

logger = logging.getLogger("stourio.mcp.registry")

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class ToolRegistry:
    """Central registry for in-process MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._interceptor = None
        self._approval_handler = None
        self._telegram_client = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool. Name must be alphanumeric, underscore, or hyphen."""
        if not _VALID_NAME_RE.match(tool.name):
            raise ValueError(
                f"Invalid tool name '{tool.name}': must match [a-zA-Z0-9_-]+"
            )
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered; overwriting.", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        """Return the tool or raise ValueError."""
        if name not in self._tools:
            raise ValueError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self, name: str, arguments: dict, agent_name: str = "unknown"
    ) -> dict:
        """Execute a registered tool by name.

        Future: interceptor/approval check will go here (Task 23).
        """
        tool = self.get(name)  # raises ValueError if missing

        if tool.execute_fn is None:
            raise ValueError(f"Tool '{name}' has no execute_fn defined.")

        logger.info("Executing tool '%s' (agent=%s)", name, agent_name)
        return await tool.execute_fn(arguments)

    # ------------------------------------------------------------------
    # LLM integration
    # ------------------------------------------------------------------

    def to_tool_definitions(self) -> list[dict]:
        """Return tool definitions in LLM tool_call format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Interceptor (placeholder for Task 23)
    # ------------------------------------------------------------------

    def set_interceptor(self, interceptor, approval_handler, telegram_client) -> None:
        """Wire up security interceptor. Implementation in Task 23."""
        self._interceptor = interceptor
        self._approval_handler = approval_handler
        self._telegram_client = telegram_client


# ------------------------------------------------------------------
# Decorator helper
# ------------------------------------------------------------------


def register_tool(
    registry: ToolRegistry,
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that registers an async function as a tool."""

    def decorator(fn: Callable[[dict], Awaitable[dict]]) -> Callable:
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters or {},
            execute_fn=fn,
        )
        registry.register(tool)
        return fn

    return decorator


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

tool_registry = ToolRegistry()

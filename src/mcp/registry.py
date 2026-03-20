"""In-process MCP Tool Registry.

Replaces the old gateway-based dispatch with direct function calls.
Security interceptor checks tool calls before execution (Task 23).
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

    def get(self, name: str) -> Tool | None:
        """Return the tool by name, or None if not found."""
        return self._tools.get(name)

    def get_strict(self, name: str) -> Tool:
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

        If a security interceptor is wired, checks the call first.
        Intercepted calls require human approval via Telegram before proceeding.
        """
        tool = self.get_strict(name)  # raises ValueError if missing

        if tool.execute_fn is None:
            raise ValueError(f"Tool '{name}' has no execute_fn defined.")

        # --- Security interceptor gate ---
        if self._interceptor is not None:
            result = await self._interceptor.check_tool_call(
                name, arguments, agent_name
            )
            if result.intercepted:
                return await self._handle_intercepted(
                    name, arguments, agent_name, result
                )

        logger.info("Executing tool '%s' (agent=%s)", name, agent_name)
        return await tool.execute_fn(arguments)

    async def _handle_intercepted(
        self, name: str, arguments: dict, agent_name: str, result
    ) -> dict:
        """Create approval request, notify via Telegram, wait for resolution."""
        from src.config import settings
        from src.models.schemas import RiskLevel

        severity_to_risk = {
            "LOW": RiskLevel.LOW,
            "MEDIUM": RiskLevel.MEDIUM,
            "HIGH": RiskLevel.HIGH,
            "CRITICAL": RiskLevel.CRITICAL,
        }
        risk = severity_to_risk.get(result.severity, RiskLevel.HIGH)

        action_desc = (
            f"Tool '{name}' called by agent '{agent_name}': {result.reason}"
        )

        # Use the approval handler if wired, otherwise fall back to module-level fn
        if self._approval_handler is not None:
            approval = await self._approval_handler.create_approval(
                action_description=action_desc,
                risk_level=risk,
            )
        else:
            from src.guardrails.approvals import create_approval_request
            approval = await create_approval_request(
                action_description=action_desc,
                risk_level=risk,
            )

        logger.warning(
            "Tool '%s' intercepted (agent=%s, severity=%s, approval=%s): %s",
            name, agent_name, result.severity, approval.id, result.reason,
        )

        # Send Telegram notification (approval via admin panel, not Telegram)
        if self._telegram_client is not None:
            try:
                chat_ids = settings.telegram_allowed_user_ids
                msg = (
                    f"Approval Required\n\n"
                    f"Tool: {name}\n"
                    f"Agent: {agent_name}\n"
                    f"Risk: {result.severity}\n"
                    f"Reason: {result.reason}\n\n"
                    f"ID: {approval.id}\n\n"
                    f"Approve or reject via admin panel."
                )
                for cid in chat_ids:
                    await self._telegram_client.send_message(
                        chat_id=cid, text=msg, parse_mode=""
                    )
            except Exception as exc:
                logger.error("Failed to send Telegram approval notification: %s", exc)

        # Wait for human decision
        timeout = settings.approval_ttl_seconds
        if self._approval_handler is not None:
            approved = await self._approval_handler.wait_for_resolution(
                approval.id, timeout_seconds=timeout
            )
        else:
            from src.guardrails.approvals import wait_for_resolution
            approved = await wait_for_resolution(
                approval.id, timeout_seconds=timeout
            )

        if not approved:
            logger.warning(
                "Tool '%s' blocked: approval %s rejected/expired", name, approval.id
            )
            return {
                "blocked": True,
                "reason": result.reason,
                "approval_id": approval.id,
            }

        # Approved — proceed with execution
        logger.info(
            "Tool '%s' approved (approval=%s), executing", name, approval.id
        )

        # MCP tools aren't in the local registry — route to MCP client
        if "__" in name:
            server_name, remote_tool_name = name.split("__", 1)
            from src.mcp.client import get_mcp_client_pool
            pool = get_mcp_client_pool()
            actual_server = server_name.lower() if pool.is_connected(server_name.lower()) else server_name
            # Strip internal keys before sending to external server
            clean_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
            return await pool.execute_tool(actual_server, remote_tool_name, clean_args)

        return await self.get(name).execute_fn(arguments)

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
    # Interceptor wiring
    # ------------------------------------------------------------------

    def set_interceptor(self, interceptor, approval_handler=None, telegram_client=None) -> None:
        """Wire up security interceptor for pre-execution checks."""
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


# ------------------------------------------------------------------
# Legacy plugin compatibility layer
# ------------------------------------------------------------------


def get_registry() -> ToolRegistry:
    """Return the global ToolRegistry singleton (replaces src.plugins.registry.get_registry)."""
    return tool_registry


def init_registry() -> ToolRegistry:
    """Load YAML/Python tools from disk and register them as MCP tools.

    Replaces the old src.plugins.registry.init_registry().
    """
    from src.config import settings
    from src.mcp.legacy.loader import load_yaml_tools, load_python_tools

    yaml_tools = load_yaml_tools(settings.tools_yaml_dir)
    python_tools = load_python_tools(settings.tools_python_dir)

    for bt in yaml_tools + python_tools:
        tool = Tool(
            name=bt.name,
            description=bt.description,
            parameters=bt.parameters,
            execute_fn=bt.execute,
        )
        tool_registry.register(tool)

    logger.info(
        "Legacy tool loader: %d tools loaded (%d YAML, %d Python).",
        len(yaml_tools) + len(python_tools),
        len(yaml_tools),
        len(python_tools),
    )
    return tool_registry

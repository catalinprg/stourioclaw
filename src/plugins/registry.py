"""ToolRegistry — central dispatch for all registered tools.

Dispatch modes:
  "local"    -> tool.execute(arguments) called in-process
  "gateway"  -> POST {mcp_server_url}/execute with Bearer token
  "sandboxed"-> treated as gateway dispatch (future extension)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

from src.plugins.base import BaseTool

if TYPE_CHECKING:
    pass

logger = logging.getLogger("stourio.plugins.registry")

# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered; overwriting.", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s (mode=%s)", tool.name, tool.execution_mode)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict]:
        return [t.to_tool_definition() for t in self._tools.values()]

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' is not registered.")

        mode = getattr(tool, "execution_mode", "local")

        if mode == "local":
            return await tool.execute(arguments)
        elif mode in ("gateway", "sandboxed"):
            return await self._execute_via_gateway(tool_name, arguments)
        else:
            logger.warning(
                "Unknown execution_mode '%s' for tool '%s'; falling back to local.",
                mode, tool_name,
            )
            return await tool.execute(arguments)

    async def _execute_via_gateway(self, tool_name: str, arguments: dict) -> dict:
        from src.config import settings  # late import to avoid circular at module load

        if not settings.mcp_server_url:
            logger.error("mcp_server_url not configured; cannot dispatch '%s' via gateway.", tool_name)
            return {"error": "MCP gateway not configured. Set MCP_SERVER_URL."}

        headers: dict[str, str] = {}
        if settings.mcp_shared_secret:
            headers["Authorization"] = f"Bearer {settings.mcp_shared_secret}"

        payload = {"tool_name": tool_name, "arguments": arguments}
        logger.info("POST %s/execute tool=%s", settings.mcp_server_url, tool_name)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{settings.mcp_server_url}/execute",
                    json=payload,
                    headers=headers,
                )
                logger.info(
                    "Gateway response: tool=%s status=%s", tool_name, response.status_code
                )
                response.raise_for_status()
                try:
                    return response.json()
                except Exception:
                    return {"result": response.text}

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Gateway HTTP %s for tool '%s': %s",
                exc.response.status_code, tool_name, exc.response.text[:200],
            )
            return {"error": f"Gateway returned {exc.response.status_code} for tool {tool_name}."}
        except httpx.HTTPError as exc:
            logger.error("Gateway network error for tool '%s': %s", tool_name, exc)
            return {"error": f"Gateway network failure: {exc}"}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the module-level singleton, creating it lazily if needed."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def init_registry() -> ToolRegistry:
    """Load tools from configured directories and return the populated registry."""
    from src.config import settings
    from src.plugins.loader import load_yaml_tools, load_python_tools

    registry = get_registry()

    yaml_tools = load_yaml_tools(settings.tools_yaml_dir)
    python_tools = load_python_tools(settings.tools_python_dir)

    for tool in yaml_tools + python_tools:
        registry.register(tool)

    logger.info(
        "Tool registry initialised: %d tools loaded (%d YAML, %d Python).",
        len(yaml_tools) + len(python_tools),
        len(yaml_tools),
        len(python_tools),
    )
    return registry

"""Tool dataclass for in-process MCP tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    """Lightweight tool descriptor with an async execute function."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    execute_fn: Callable[[dict], Awaitable[dict]] | None = None

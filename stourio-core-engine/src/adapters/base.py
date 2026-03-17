from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from typing import Any
from src.models.schemas import ChatMessage, ToolDefinition
import asyncio
import time

logger = logging.getLogger("stourio.adapters")


class LLMResponse:
    """Normalized response from any LLM provider."""

    def __init__(
        self,
        text: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        raw: Any = None,
    ):
        self.text = text
        self.tool_calls = self._validate_tools(tool_calls or [])
        self.raw = raw

    def _validate_tools(self, calls: list[dict]) -> list[dict]:
        """Parse string arguments and drop malformed tool calls."""
        valid_calls = []
        for call in calls:
            try:
                if isinstance(call.get("arguments"), str):
                    call["arguments"] = json.loads(call["arguments"])
                valid_calls.append(call)
            except json.JSONDecodeError:
                logger.error(f"Adapter dropped malformed tool call: {call}")
                continue
        return valid_calls

    @property
    def has_tool_call(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def first_tool_call(self) -> dict[str, Any] | None:
        return self.tool_calls[0] if self.tool_calls else None


class BaseLLMAdapter(ABC):
    """
    Abstract interface for all LLM providers.
    Every adapter normalizes provider-specific API differences
    into a single request/response format.
    """

    provider_name: str = "base"

    def __init__(self):
        # Per-instance rate limiter state (not shared across adapters)
        self._tokens: float = 2.0
        self._last_refill: float = time.time()
        self._rate: float = 0.25      # Tokens per second
        self._capacity: float = 2.0   # Max burst
        self._lock = asyncio.Lock()

    async def _acquire_rate_limit_token(self):
        """Simple token bucket rate limiter to prevent 429 errors."""
        async with self._lock:
            now = time.time()
            passed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + (passed * self._rate))
            self._last_refill = now

            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self._rate
                logger.warning(f"Rate limit hit for {self.provider_name}. Waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self._tokens = 0
            else:
                self._tokens -= 1

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """Send a completion request and return a normalized response."""
        ...

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Override per provider to format tool definitions."""
        return [t.model_dump() for t in tools]

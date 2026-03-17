from __future__ import annotations
import json
from anthropic import AsyncAnthropic
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition, TokenUsage


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic Claude models."""

    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.model = model
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        await self._acquire_rate_limit_token()
        formatted_messages = []
        for m in messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": self.model,
            "system": system_prompt,
            "messages": formatted_messages,
            "max_tokens": 4096,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = self._format_tools(tools)

        response = await self.client.messages.create(**kwargs)

        # Extract text and tool calls from content blocks
        text_parts = []
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            raw={},
            usage=TokenUsage(
                input_tokens=getattr(response.usage, 'input_tokens', 0) if response.usage else 0,
                output_tokens=getattr(response.usage, 'output_tokens', 0) if response.usage else 0,
                total_tokens=(
                    getattr(response.usage, 'input_tokens', 0) +
                    getattr(response.usage, 'output_tokens', 0)
                ) if response.usage else 0,
            ),
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Anthropic tool format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters or {"type": "object", "properties": {}},
            }
            for t in tools
        ]

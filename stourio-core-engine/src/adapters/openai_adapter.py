from __future__ import annotations
import json
from openai import AsyncOpenAI
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for OpenAI and any OpenAI-compatible API (DeepSeek, Ollama, etc.)."""

    provider_name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        super().__init__()
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        await self._acquire_rate_limit_token()
        formatted_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature,
        }

        if tools:
            kwargs["tools"] = self._format_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract tool calls if present
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return LLMResponse(
            text=choice.message.content,
            tool_calls=tool_calls,
            raw=response,
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

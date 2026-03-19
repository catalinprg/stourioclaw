"""OpenRouter LLM adapter — single gateway to all model providers."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition, TokenUsage

logger = logging.getLogger("stourio.adapters.openrouter")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterAdapter(BaseLLMAdapter):
    """Routes completions through OpenRouter to any supported model."""

    provider_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_models: list[str] | None = None,
    ):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.fallback_models = fallback_models or []
        self._client = httpx.AsyncClient(timeout=120.0)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        await self._acquire_rate_limit_token()

        formatted_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        for m in messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = self._format_tools(tools)
            payload["tool_choice"] = "auto"

        # Fallback routing: send models array + route flag
        if self.fallback_models:
            payload["route"] = "fallback"
            payload["models"] = [self.model] + self.fallback_models

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.debug("OpenRouter request: model=%s fallback=%s", self.model, bool(self.fallback_models))

        response = await self._client.post(
            OPENROUTER_API_URL,
            json=payload,
            headers=headers,
        )

        if response.status_code != 200:
            body = response.text
            logger.error("OpenRouter API error %d: %s", response.status_code, body)
            raise Exception(f"OpenRouter API error {response.status_code}: {body}")

        data = response.json()
        return self._parse_response(data)

    def _parse_response(self, data: dict) -> LLMResponse:
        """Parse OpenAI-format response into normalized LLMResponse."""
        choice = data["choices"][0]
        message = choice["message"]

        # Extract tool calls (OpenAI format)
        tool_calls: list[dict] = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],  # LLMResponse validator handles JSON parsing
                })

        usage_data = data.get("usage", {})

        return LLMResponse(
            text=message.get("content"),
            tool_calls=tool_calls,
            raw=data,
            usage=TokenUsage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            ),
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

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

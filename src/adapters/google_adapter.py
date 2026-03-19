from __future__ import annotations
import json
from google import genai
from google.genai import types
from src.adapters.base import BaseLLMAdapter, LLMResponse
from src.models.schemas import ChatMessage, ToolDefinition, TokenUsage


class GoogleAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini models."""

    provider_name = "google"

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.model = model
        self.client = genai.Client(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        # Build contents
        await self._acquire_rate_limit_token()
        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))

        # Build config
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        )

        if tools:
            config.tools = self._format_tools(tools)

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        # Extract text and tool calls
        text_parts = []
        tool_calls = []

        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append({
                        "id": fc.name,
                        "name": fc.name,
                        "arguments": dict(fc.args) if fc.args else {},
                    })

        usage_meta = getattr(response, 'usage_metadata', None)
        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            raw={},
            usage=TokenUsage(
                input_tokens=getattr(usage_meta, 'prompt_token_count', 0) if usage_meta else 0,
                output_tokens=getattr(usage_meta, 'candidates_token_count', 0) if usage_meta else 0,
                total_tokens=getattr(usage_meta, 'total_token_count', 0) if usage_meta else 0,
            ),
        )

    def _format_tools(self, tools: list[ToolDefinition]) -> list[types.Tool]:
        """Google function declaration format."""
        declarations = []
        for t in tools:
            declarations.append(types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.parameters or {"type": "object", "properties": {}},
            ))
        return [types.Tool(function_declarations=declarations)]

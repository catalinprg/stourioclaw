import hashlib
import json
import logging
from redis.asyncio import Redis
from src.adapters.base import BaseLLMAdapter, LLMResponse

logger = logging.getLogger("stourio.adapters.cache")


def build_cache_key(provider, model, system_prompt, messages, tools) -> str:
    """Deterministic hash including system_prompt to prevent cross-contamination."""
    payload = {
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "messages": [m.model_dump() if hasattr(m, "model_dump") else m for m in messages],
        "tools": [
            t.model_dump() if hasattr(t, "model_dump") else t
            for t in sorted(tools, key=lambda t: getattr(t, "name", str(t)))
        ] if tools else None,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"stourio:llm_cache:{content_hash}"


class CachedLLMAdapter:
    """Decorator around any LLM adapter. Transparent caching layer."""

    def __init__(self, adapter: BaseLLMAdapter, redis: Redis, ttl: int = 300):
        self.adapter = adapter
        self.redis = redis
        self.ttl = ttl
        self.provider_name = adapter.provider_name

    async def complete(self, system_prompt, messages, tools=None, temperature=0.1):
        if self.ttl <= 0:
            return await self.adapter.complete(system_prompt, messages, tools, temperature)

        key = build_cache_key(
            self.adapter.provider_name,
            getattr(self.adapter, "model", "unknown"),
            system_prompt,
            messages,
            tools,
        )

        cached = await self.redis.get(key)
        if cached:
            logger.debug(f"Cache HIT: {key[:40]}...")
            data = json.loads(cached)
            return LLMResponse(
                text=data.get("text"),
                tool_calls=data.get("tool_calls"),
                raw=data.get("raw", {}),
            )

        result = await self.adapter.complete(system_prompt, messages, tools, temperature)

        try:
            cache_data = json.dumps(
                {"text": result.text, "tool_calls": result.tool_calls, "raw": {}},
                default=str,
            )
            await self.redis.setex(key, self.ttl, cache_data)
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")

        return result

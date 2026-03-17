from __future__ import annotations
import logging
from src.adapters.base import BaseLLMAdapter
from src.adapters.openai_adapter import OpenAIAdapter
from src.adapters.anthropic_adapter import AnthropicAdapter
from src.adapters.google_adapter import GoogleAdapter
from src.config import settings

logger = logging.getLogger("stourio.adapters")


def create_adapter(provider: str, model: str) -> BaseLLMAdapter:
    """Factory: create an LLM adapter based on provider name."""

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set")
        return OpenAIAdapter(
            api_key=settings.openai_api_key,
            model=model,
        )

    elif provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        return AnthropicAdapter(
            api_key=settings.anthropic_api_key,
            model=model,
        )

    elif provider == "deepseek":
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY not set")
        return OpenAIAdapter(
            api_key=settings.deepseek_api_key,
            model=model or settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )

    elif provider == "google":
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY not set")
        return GoogleAdapter(
            api_key=settings.google_api_key,
            model=model or settings.google_model,
        )

    else:
        raise ValueError(f"Unknown provider: {provider}")


# Orchestrator adapter singleton (handles routing, not tied to a specific agent)
_orchestrator_adapter: BaseLLMAdapter | None = None

# Cache for dynamically instantiated agent adapters
_adapter_cache: dict[tuple[str, str], BaseLLMAdapter] = {}


def get_orchestrator_adapter() -> BaseLLMAdapter:
    global _orchestrator_adapter
    if _orchestrator_adapter is None:
        _orchestrator_adapter = create_adapter(
            settings.orchestrator_provider, settings.orchestrator_model
        )
        logger.info(
            f"Orchestrator adapter: {settings.orchestrator_provider} / {settings.orchestrator_model}"
        )
    return _orchestrator_adapter


def get_agent_adapter(provider: str | None = None, model: str | None = None) -> BaseLLMAdapter:
    """
    Returns an adapter from the cache, instantiating it if it doesn't exist.
    Falls back to environment variables if overrides are not provided.
    """
    p = provider or settings.agent_provider
    m = model or settings.agent_model

    key = (p, m)
    if key not in _adapter_cache:
        _adapter_cache[key] = create_adapter(p, m)
        logger.info(f"Initialized dynamic agent adapter: {p} / {m}")

    return _adapter_cache[key]
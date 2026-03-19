"""Adapter registry — all LLM calls go through OpenRouter."""
from __future__ import annotations

import logging

from src.adapters.openrouter import OpenRouterAdapter
from src.config import settings

logger = logging.getLogger("stourio.adapters")

# Singletons
_orchestrator_adapter: OpenRouterAdapter | None = None
_adapter_cache: dict[str, OpenRouterAdapter] = {}


def get_orchestrator_adapter() -> OpenRouterAdapter:
    """Return the cached orchestrator adapter (creates on first call)."""
    global _orchestrator_adapter
    if _orchestrator_adapter is None:
        _orchestrator_adapter = OpenRouterAdapter(
            api_key=settings.openrouter_api_key,
            model=settings.orchestrator_model,
        )
        logger.info("Orchestrator adapter: openrouter / %s", settings.orchestrator_model)
    return _orchestrator_adapter


def get_agent_adapter(model: str) -> OpenRouterAdapter:
    """Return a cached adapter for the given model string."""
    if model not in _adapter_cache:
        fallback = (
            settings.openrouter_fallback_models
            if settings.openrouter_fallback_enabled
            else []
        )
        _adapter_cache[model] = OpenRouterAdapter(
            api_key=settings.openrouter_api_key,
            model=model,
            fallback_models=fallback,
        )
        logger.info("Agent adapter: openrouter / %s (fallback=%s)", model, bool(fallback))
    return _adapter_cache[model]


def reset_adapters() -> None:
    """Clear all cached adapters. Used in testing."""
    global _orchestrator_adapter, _adapter_cache
    _orchestrator_adapter = None
    _adapter_cache = {}

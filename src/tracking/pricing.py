MODEL_PRICING: dict[str, dict[str, float]] = {
    # Direct provider model strings
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 5.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    "rerank-v3.5": {"input": 0.0, "output": 0.0, "per_search": 0.001},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    # OpenRouter model strings
    "anthropic/claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "anthropic/claude-opus-4-20250918": {"input": 15.00, "output": 75.00},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0, units: int = 0) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0
    cost = (input_tokens * pricing.get("input", 0) / 1_000_000) + \
           (output_tokens * pricing.get("output", 0) / 1_000_000) + \
           (units * pricing.get("per_search", 0))
    return round(cost, 6)

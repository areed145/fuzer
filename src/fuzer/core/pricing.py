"""Provider prices, for costs Fuzer shows next to assistant and reader usage.

$ per million tokens: input, output, cache read, cache write. Update when prices change; an unknown
hosted model is reported as unpriced, and Ollama is free.
"""

PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
OPENAI_PRICES = {
    "gpt-4o": (2.50, 10.00, 1.25, 0.0),
    "gpt-4o-mini": (0.15, 0.60, 0.075, 0.0),
    "gpt-4.1": (2.00, 8.00, 0.50, 0.0),
    "gpt-4.1-mini": (0.40, 1.60, 0.10, 0.0),
    "gpt-4.1-nano": (0.10, 0.40, 0.025, 0.0),
    "o3": (2.00, 8.00, 0.50, 0.0),
    "o4-mini": (1.10, 4.40, 0.275, 0.0),
}
PROVIDER_PRICES = {"anthropic": PRICES, "openai": OPENAI_PRICES,
                   "azure_openai": OPENAI_PRICES, "ollama": {}}
FALLBACK = "claude-opus-5-5"


def cost(model: str, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
         cache_write: int = 0, provider: str | None = None) -> tuple[float, bool]:
    """(dollars, whether the model has a listed price)."""
    prices = PROVIDER_PRICES.get(provider, PRICES)
    if provider == "ollama":
        return 0.0, True
    if provider not in (None, "anthropic", "openai", "azure_openai"):
        return 0.0, False
    p = prices.get(model.lower() if provider in ("openai", "azure_openai") else model)
    known = p is not None
    if not known and provider in ("openai", "azure_openai"):
        return 0.0, False
    p = p or PRICES[FALLBACK]
    return (input_tokens * p[0] + output_tokens * p[1] + cache_read * p[2] + cache_write * p[3]) / 1e6, known

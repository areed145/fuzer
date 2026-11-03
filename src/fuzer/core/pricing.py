"""Claude prices, for the cost Fuzer shows next to anything it spends (assistant answers, Claude reading).

$ per million tokens: input, output, cache read, cache write (5 minutes). Update when prices change;
a model not listed here is priced as Claude Opus 5.5 and reported as unpriced, so it doesn't pass
unnoticed.
"""

PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
FALLBACK = "claude-opus-5-5"


def cost(model: str, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
         cache_write: int = 0) -> tuple[float, bool]:
    """(dollars, whether the model has a listed price)."""
    p = PRICES.get(model)
    known = p is not None
    p = p or PRICES[FALLBACK]
    return (input_tokens * p[0] + output_tokens * p[1] + cache_read * p[2] + cache_write * p[3]) / 1e6, known

"""uv run python -m fuzer.agents.capabilities > docs/capabilities.md"""

from . import catalogue

print(catalogue(), end="")

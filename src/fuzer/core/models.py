"""The Claude models Fuzer uses when nothing else is set (one place; the settings under #131 default to these)."""

DEFAULT_MODEL = "claude-sonnet-5-5"  # every call, at medium effort
PID_MODEL = "claude-opus-5-5"  # P&ID drawing reads only: reading symbols, tags and lines off a sheet needs the larger model
DEFAULT_EFFORT = "medium"

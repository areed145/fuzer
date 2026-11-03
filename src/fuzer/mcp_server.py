"""Moved to fuzer.agents.mcp_server (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.mcp_server moved to fuzer.agents.mcp_server", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.agents.mcp_server")

"""Moved to fuzer.agents.assistant (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.assistant moved to fuzer.agents.assistant", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.agents.assistant")

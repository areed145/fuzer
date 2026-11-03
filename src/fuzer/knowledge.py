"""Moved to fuzer.graph.knowledge (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.knowledge moved to fuzer.graph.knowledge", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.knowledge")

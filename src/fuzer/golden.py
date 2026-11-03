"""Moved to fuzer.graph.golden (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.golden moved to fuzer.graph.golden", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.golden")

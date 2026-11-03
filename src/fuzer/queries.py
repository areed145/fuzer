"""Moved to fuzer.graph.queries (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.queries moved to fuzer.graph.queries", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.queries")

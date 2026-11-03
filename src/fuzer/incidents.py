"""Moved to fuzer.graph.incidents (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.incidents moved to fuzer.graph.incidents", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.incidents")

"""Moved to fuzer.graph.taxonomy (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.taxonomy moved to fuzer.graph.taxonomy", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.taxonomy")

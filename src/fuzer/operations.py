"""Moved to fuzer.graph.operations (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.operations moved to fuzer.graph.operations", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.operations")

"""Moved to fuzer.graph.spatial (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.spatial moved to fuzer.graph.spatial", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.spatial")

"""Moved to fuzer.graph.resolve (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.resolve moved to fuzer.graph.resolve", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.resolve")

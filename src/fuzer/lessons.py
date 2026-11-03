"""Moved to fuzer.graph.lessons (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.lessons moved to fuzer.graph.lessons", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.lessons")

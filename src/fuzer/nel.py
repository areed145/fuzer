"""Moved to fuzer.graph.nel (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.nel moved to fuzer.graph.nel", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.nel")

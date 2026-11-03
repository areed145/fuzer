"""Moved to fuzer.graph.measurements (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.measurements moved to fuzer.graph.measurements", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.measurements")

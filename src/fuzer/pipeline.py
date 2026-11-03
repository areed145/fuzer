"""Moved to fuzer.graph.pipeline (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.pipeline moved to fuzer.graph.pipeline", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.pipeline")

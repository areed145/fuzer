"""Moved to fuzer.analysis.clusters (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.clusters moved to fuzer.analysis.clusters", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.clusters")

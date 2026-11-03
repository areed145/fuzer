"""Moved to fuzer.analysis.equipment (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.equipment moved to fuzer.analysis.equipment", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.equipment")

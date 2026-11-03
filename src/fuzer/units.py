"""Moved to fuzer.core.units (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.units moved to fuzer.core.units", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.core.units")

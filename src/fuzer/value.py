"""Moved to fuzer.analysis.value (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.value moved to fuzer.analysis.value", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.value")

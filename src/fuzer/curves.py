"""Moved to fuzer.analysis.curves (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.curves moved to fuzer.analysis.curves", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.curves")

"""Moved to fuzer.analysis.cases (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.cases moved to fuzer.analysis.cases", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.cases")

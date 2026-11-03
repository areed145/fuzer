"""Moved to fuzer.analysis.calcs (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.calcs moved to fuzer.analysis.calcs", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.calcs")

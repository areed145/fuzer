"""Moved to fuzer.analysis.hydraulics (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.hydraulics moved to fuzer.analysis.hydraulics", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.hydraulics")

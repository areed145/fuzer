"""Moved to fuzer.analysis.signals (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.signals moved to fuzer.analysis.signals", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.signals")

"""Moved to fuzer.analysis.alarms (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.alarms moved to fuzer.analysis.alarms", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.alarms")

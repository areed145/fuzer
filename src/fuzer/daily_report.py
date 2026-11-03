"""Moved to fuzer.ops.daily_report (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.daily_report moved to fuzer.ops.daily_report", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.daily_report")

"""Moved to fuzer.sources.timeseries (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.timeseries moved to fuzer.sources.timeseries", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.timeseries")

"""Moved to fuzer.sources.curves (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.curves moved to fuzer.sources.curves", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.curves")

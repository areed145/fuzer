"""Moved to fuzer.sources.adx (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.adx moved to fuzer.sources.adx", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.adx")

"""Moved to fuzer.sources.extract (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.extract moved to fuzer.sources.extract", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.extract")

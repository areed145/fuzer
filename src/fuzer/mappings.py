"""Moved to fuzer.sources.mappings (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.mappings moved to fuzer.sources.mappings", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.mappings")

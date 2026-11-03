"""Moved to fuzer.core.pack (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.pack moved to fuzer.core.pack", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.core.pack")

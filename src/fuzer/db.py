"""Moved to fuzer.core.db (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.db moved to fuzer.core.db", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.core.db")

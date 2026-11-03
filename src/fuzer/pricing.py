"""Moved to fuzer.core.pricing (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.pricing moved to fuzer.core.pricing", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.core.pricing")

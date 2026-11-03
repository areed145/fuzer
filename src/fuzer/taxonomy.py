"""Moved to fuzer.core.taxonomy (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.taxonomy moved to fuzer.core.taxonomy", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.core.taxonomy")

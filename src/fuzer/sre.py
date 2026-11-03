"""Moved to fuzer.ops.sre (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.sre moved to fuzer.ops.sre", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.sre")

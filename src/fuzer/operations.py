"""Moved to fuzer.ops.operations (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.operations moved to fuzer.ops.operations", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.operations")

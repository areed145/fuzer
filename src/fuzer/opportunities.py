"""Moved to fuzer.ops.opportunities (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.opportunities moved to fuzer.ops.opportunities", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.opportunities")

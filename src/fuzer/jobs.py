"""Moved to fuzer.ops.jobs (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.jobs moved to fuzer.ops.jobs", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.jobs")

"""Moved to fuzer.evals.evaluate (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.evaluate moved to fuzer.evals.evaluate", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.evals.evaluate")

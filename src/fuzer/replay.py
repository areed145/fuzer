"""Moved to fuzer.evals.replay (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.replay moved to fuzer.evals.replay", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.evals.replay")

"""Moved to fuzer.evals.ops_eval (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.ops_eval moved to fuzer.evals.ops_eval", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.evals.ops_eval")

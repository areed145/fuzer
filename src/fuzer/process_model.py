"""Moved to fuzer.analysis.process_model (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.process_model moved to fuzer.analysis.process_model", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.process_model")

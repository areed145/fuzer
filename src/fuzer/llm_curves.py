"""Moved to fuzer.sources.llm_curves (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.llm_curves moved to fuzer.sources.llm_curves", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.llm_curves")

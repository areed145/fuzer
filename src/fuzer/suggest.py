"""Moved to fuzer.sources.suggest (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.suggest moved to fuzer.sources.suggest", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.suggest")

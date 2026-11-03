"""Moved to fuzer.sources.connectors (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.connectors moved to fuzer.sources.connectors", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.connectors")

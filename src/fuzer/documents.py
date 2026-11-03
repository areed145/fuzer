"""Moved to fuzer.sources.documents (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.documents moved to fuzer.sources.documents", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.documents")

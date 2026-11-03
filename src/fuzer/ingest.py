"""Moved to fuzer.sources.ingest (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.ingest moved to fuzer.sources.ingest", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.ingest")

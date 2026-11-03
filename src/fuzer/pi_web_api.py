"""Moved to fuzer.sources.pi_web_api (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.pi_web_api moved to fuzer.sources.pi_web_api", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.pi_web_api")

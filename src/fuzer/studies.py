"""Moved to fuzer.analysis.studies (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.studies moved to fuzer.analysis.studies", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.analysis.studies")

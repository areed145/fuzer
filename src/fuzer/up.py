"""Moved to fuzer.cli.up (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.up moved to fuzer.cli.up", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.cli.up")

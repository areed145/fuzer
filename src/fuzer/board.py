"""Moved to fuzer.ops.board (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.board moved to fuzer.ops.board", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.ops.board")

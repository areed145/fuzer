"""Moved to fuzer.sources.llm_pids (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.llm_pids moved to fuzer.sources.llm_pids", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.llm_pids")

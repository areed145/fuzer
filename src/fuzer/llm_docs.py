"""Moved to fuzer.sources.llm_docs (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.llm_docs moved to fuzer.sources.llm_docs", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.sources.llm_docs")

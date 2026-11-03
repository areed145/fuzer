"""Moved to fuzer.graph.llm_links (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.llm_links moved to fuzer.graph.llm_links", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.graph.llm_links")

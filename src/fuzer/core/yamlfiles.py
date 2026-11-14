"""YAML files the checkout ships (bundles, packs, evals), parsed once per version of each file (#1069).

A case page asked which work types the specialist packs brief, and answering read every pack's manifest and every
shipped bundle again: about 150 YAML parses, 0.4 s of CPU on a quiet laptop and seconds on the shared server, all
of it holding the interpreter while other requests waited. The parse is kept per (path, modification time, size),
so an edited file is read again, and each caller gets its own copy to change as it likes."""

import copy
import functools
from pathlib import Path

import yaml


def load(path: str | Path):
    """The file's YAML, as yaml.safe_load gives it (raises the same errors), from memory when the file is unchanged."""
    p = Path(path)
    st = p.stat()
    return copy.deepcopy(_parsed(str(p.resolve()), st.st_mtime_ns, st.st_size))


@functools.lru_cache(maxsize=1024)
def _parsed(path: str, mtime_ns: int, size: int):
    return yaml.safe_load(Path(path).read_text())

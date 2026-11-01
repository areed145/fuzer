"""Units of measure from reference data (units.yaml): measurements are stored in one unit per
quantity, whatever spelling or unit system the source used."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

UNITS = Path(__file__).with_name("units.yaml")


@lru_cache(maxsize=1)
def table() -> dict[str, tuple[str, float, float]]:
    """Every known spelling (case-sensitive, then lower-case) -> (stored unit, factor, offset)."""
    out: dict[str, tuple[str, float, float]] = {}
    spec = {unit: s or {} for unit, s in yaml.safe_load(UNITS.read_text()).items()}
    for unit, s in spec.items():  # a stored unit is never converted, even if listed under another
        for name in [unit, *s.get("aliases", [])]:
            out[str(name)] = (unit, 1.0, 0.0)
    for unit, s in spec.items():
        for src, conv in (s.get("from") or {}).items():
            for name in [src, *conv.get("aliases", [])]:
                out.setdefault(str(name), (unit, float(conv["factor"]), float(conv.get("offset", 0))))
    for name, v in list(out.items()):
        out.setdefault(name.lower(), v)
    return out


def normalise(value: float, unit: str | None) -> tuple[float, str | None]:
    """The value in its stored unit. Units not in the table are kept as given."""
    if not unit:
        return value, unit
    unit = unit.strip()
    found = table().get(unit) or table().get(unit.lower())
    if not found:
        return value, unit
    to, factor, offset = found
    return value * factor + offset, to


NUMBER = re.compile(r"^\s*([<>]=?|~)?\s*(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)\s*$")


def reading(raw) -> tuple[float, str | None] | None:
    """A reported value as (number, qualifier): 12.5 -> (12.5, None), "<0.5" (below the detection
    limit) -> (0.5, "<"). None for blanks and text that isn't a number."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw), None
    m = NUMBER.match(str(raw).replace(",", ""))
    return (float(m.group(2)), m.group(1)) if m else None

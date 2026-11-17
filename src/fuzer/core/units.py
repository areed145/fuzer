"""Units of measure from reference data (units.yaml): measurements are stored in one unit per
quantity, whatever spelling or unit system the source used."""

import re
from functools import lru_cache

import yaml

from ..resources import UNITS

# Barrels of oil equivalent: 6 Mscf of gas is one barrel of oil (the US 6:1 energy convention), so figures that
# combine oil and gas (production, deferment, losses) are in BOE and BOE/d.
MSCF_PER_BOE = 6.0
# Unit conversions the analysis does in SI inside a correlation (the stored units are US oilfield ones).
PSI_PER_BAR = 14.5038
BBL_PER_SM3 = 6.28981
MSCF_PER_KSM3 = 35.3147
MM_PER_IN = 25.4


def boe(oil_bbl: float | None = 0.0, gas_mscf: float | None = 0.0) -> float:
    """Oil (bbl) and gas (Mscf) together as barrels of oil equivalent; also bbl/d and Mscf/d as BOE/d."""
    return float(oil_bbl or 0.0) + float(gas_mscf or 0.0) / MSCF_PER_BOE


def f_to_c(t: float) -> float:
    return (t - 32.0) / 1.8


def c_to_f(t: float) -> float:
    return t * 1.8 + 32.0


@lru_cache(maxsize=1)
def table() -> dict[str, tuple[str, float, float]]:
    """Every known spelling (case-sensitive, then lower-case) -> (stored unit, factor, offset). A non-linear
    conversion (`api`: a density as API gravity) has a factor of 0 and its density scale as the offset."""
    out: dict[str, tuple[str, float, float]] = {}
    spec = {unit: s or {} for unit, s in yaml.safe_load(UNITS.read_text()).items()}
    for unit, s in spec.items():  # a stored unit is never converted, even if listed under another
        for name in [unit, *s.get("aliases", [])]:
            out[str(name)] = (unit, 1.0, 0.0)
    for unit, s in spec.items():
        for src, conv in (s.get("from") or {}).items():
            for name in [src, *conv.get("aliases", [])]:
                if "api" in conv:
                    out.setdefault(str(name), (unit, 0.0, float(conv["api"])))
                else:
                    out.setdefault(str(name), (unit, float(conv["factor"]), float(conv.get("offset", 0))))
    for name, v in list(out.items()):
        out.setdefault(name.lower(), v)
    return out


def _apply(value: float, factor: float, offset: float) -> float:
    if factor == 0.0:  # a density (kg/m3 x offset) as API gravity at 60 F (water 999.016 kg/m3)
        return 141.5 / (value * offset / 999.016) - 131.5 if value else value
    return value * factor + offset


def normalise(value: float, unit: str | None) -> tuple[float, str | None]:
    """The value in its stored unit. Units not in the table are kept as given."""
    if not unit:
        return value, unit
    unit = unit.strip()
    found = table().get(unit) or table().get(unit.lower())
    if not found:
        return value, unit
    to, factor, offset = found
    return _apply(value, factor, offset), to


def convert(value: float, unit: str | None, to: str) -> float | None:
    """A value in `unit` in another unit of the same stored quantity (psig -> barg, degF -> degC), through the
    stored unit; None when the two aren't the same quantity in the table or the way back isn't linear."""
    v, stored = normalise(value, unit)
    found = table().get(to) or table().get(to.lower())
    if stored is None or not found or found[0] != stored or found[1] == 0.0:
        return None
    return (v - found[2]) / found[1]


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


DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|mins?|minutes?|h|hrs?|hours?|d|days?)\s*$", re.IGNORECASE)
PER = {"m": 1, "h": 60, "d": 1440}


def minutes(raw) -> float | None:
    """A duration as written in registers ("2 h", "4 hrs", "120 min", "1 day") in minutes; a bare
    number is hours. None for blanks and text that isn't one."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw) * 60
    m = DURATION.match(str(raw))
    if not m:
        r = reading(raw)
        return r[0] * 60 if r else None
    return float(m.group(1)) * PER[m.group(2)[0].lower()]

"""US oilfield units for a fake plant's exports: psig, °F, bbl/d, Mscf/d, in, mpy, ft.

The fake plants' process models and generators work in SI internally (their correlations are written that way);
what they hand to Fuzer is what a US operator's systems would export. `convert_dir(folder)` rewrites a folder of
exports in place: columns named for a metric unit (`whp_barg`, `oil_sm3d`) become their US twins (`whp_psig`,
`oil_bbld`) with the values converted, a value beside a unit column (`unit`, `engunits`, `eu`, `x_unit`) is
converted with its unit, and quantities written in free text (operator logs, notification text, procedures) read
in US units. `fakeplant generate` runs it over Okan after writing; the hand-written bundles were converted with
`fakeplant oilfield <folder>` once and are kept in US units.

Where a source mixed its units on purpose (older inspection surveys in inches, a lab's few results in degF, the
1998 design basis in US units), the odd rows swap the other way, into metric (SWAP), so the mix Fuzer normalises on
the way in is still there.
"""

import csv
import io
import json
import math
import re
from pathlib import Path

from fuzer.core import units

# Metric spelling -> US spelling: the stored unit of units.yaml unless a source would say it another way.
US = {"barg": "psig", "bara": "psia", "bar": "psi", "mbarg": "psig", "mbar": "psi", "kPa": "psi", "kPag": "psig",
      "Sm3/d": "bbl/d", "sm3/d": "bbl/d", "m3/d": "bbl/d", "Sm3": "bbl", "sm3": "bbl", "m3": "bbl",
      "kSm3/d": "Mscf/d", "ksm3/d": "Mscf/d", "kSm3": "Mscf", "ksm3": "Mscf", "Sm3/h": "scfh", "sm3/h": "scfh",
      "Sm3/Sm3": "scf/bbl", "rm3/Sm3": "rb/STB", "kSm3/Sm3": "Mscf/bbl",
      "degC": "degF", "deg C": "degF", "°C": "°F",
      "mm": "in", "um": "mils", "mm/y": "mpy", "mm/yr": "mpy", "mm/s": "in/s", "mm/s RMS": "in/s RMS",
      "m": "ft", "km": "mi", "kW": "hp", "MW": "MMBtu/h", "m3/h": "gpm", "Am3/h": "acfm", "Nm3/h": "scfm",
      "mg/Sm3": "lb/MMscf", "kg/m3": "API", "MJ/Sm3": "Btu/scf", "kJ/kg": "ft-lbf/lbm", "kJ/kWh": "Btu/kWh",
      "kN": "lbf", "degC/week": "degF/week"}
# Conversions units.yaml doesn't store (a unit only ever printed): factor, offset.
EXTRA = {("rm3/Sm3", "rb/STB"): (1.0, 0.0), ("kSm3/Sm3", "Mscf/bbl"): (5.61458, 0.0),
         ("MW", "MMBtu/h"): (3.41214, 0.0), ("mm/s RMS", "in/s RMS"): (0.0393701, 0.0),
         ("degC/week", "degF/week"): (1.8, 0.0)}
# A temperature difference converts without the offset.
DELTA_C = {"degC from the others", "degC above", "degC below", "degC rise", "degC hotter", "degC cooler"}

# Columns named for a metric unit: metric name -> (US name, metric unit).
COLUMNS = {
    "oil_sm3d": ("oil_bbld", "Sm3/d"), "condensate_sm3d": ("condensate_bbld", "Sm3/d"),
    "water_sm3d": ("water_bbld", "Sm3/d"), "liquid_sm3d": ("liquid_bbld", "Sm3/d"),
    "gas_ksm3d": ("gas_mscfd", "kSm3/d"), "lift_gas_ksm3d": ("lift_gas_mscfd", "kSm3/d"),
    "gor_sm3_sm3": ("gor_scf_bbl", "Sm3/Sm3"), "whp_barg": ("whp_psig", "barg"),
    "pressure_barg": ("pressure_psig", "barg"), "mop_barg": ("mop_psig", "barg"),
    "lost_oil_sm3": ("lost_oil_bbl", "Sm3"), "lost_gas_ksm3": ("lost_gas_mscf", "kSm3"),
    "lost_condensate_sm3": ("lost_condensate_bbl", "Sm3"),
    "oil_sm3": ("oil_bbl", "Sm3"), "oil_potential_sm3": ("oil_potential_bbl", "Sm3"),
    "condensate_sm3": ("condensate_bbl", "Sm3"), "water_sm3": ("water_bbl", "Sm3"),
    "gas_ksm3": ("gas_mscf", "kSm3"), "water_inj_sm3": ("water_inj_bbl", "Sm3"),
    "gas_export_ksm3": ("gas_export_mscf", "kSm3"), "lift_gas_ksm3": ("lift_gas_mscf", "kSm3"),
    "flare_ksm3": ("flare_mscf", "kSm3"), "fuel_gas_ksm3": ("fuel_gas_mscf", "kSm3"),
    "vent_ksm3": ("vent_mscf", "kSm3"), "volume_ksm3": ("volume_mscf", "kSm3"),
    "hhv_mj_sm3": ("hhv_btu_scf", "MJ/Sm3"), "energy_gj": ("energy_mmbtu", "GJ"),
    "length_mm": ("length_in", "mm"), "width_mm": ("width_in", "mm"), "depth_mm": ("depth_in", "mm"),
    "element_diameter_mm": ("element_diameter_in", "mm"), "pitch_diameter_mm": ("pitch_diameter_in", "mm"),
    "rate_mm_y": ("rate_mpy", "mm/y"), "remaining_mm": ("remaining_in", "mm"), "size_mm": ("size_in", "mm"),
    "wall_thickness_mm": ("wall_thickness_in", "mm"), "nominal_mm": ("nominal_in", "mm"),
    "min_required_mm": ("min_required_in", "mm"), "retirement_mm": ("retirement_in", "mm"),
    "station_m": ("station_ft", "m"), "station_from_m": ("station_from_ft", "m"), "station_to_m": ("station_to_ft", "m"),
    "md_m": ("md_ft", "m"), "tvd_m": ("tvd_ft", "m"), "top_md_m": ("top_md_ft", "m"), "base_md_m": ("base_md_ft", "m"),
    "water_depth_m": ("water_depth_ft", "m"), "length_km": ("length_mi", "km"), "kp": ("mp", "km"),
    "motor_rated_kw": ("motor_rated_hp", "kW"), "rated_kw": ("rated_hp", "kW"),
    "Dry Weight (kg)": ("Dry Weight (lb)", "kg"),
    "limit_sm3d": ("limit_bbld", "Sm3/d"), "initial_barg": ("initial_psig", "barg"),
    "k_bar_per_rm3": ("k_psi_per_rb", "bar/rm3"), "pi": ("pi", "Sm3/d/bar"),
}
EXTRA[("bar/rm3", "psi/rb")] = (14.5038 / 6.28981, 0.0)
EXTRA[("Sm3/d/bar", "bbl/d/psi")] = (6.28981 / 14.5038, 0.0)
US |= {"bar/rm3": "psi/rb", "Sm3/d/bar": "bbl/d/psi"}
EXTRA[("kg", "lb")] = (2.20462, 0.0)
EXTRA[("GJ", "MMBtu")] = (0.947817, 0.0)
EXTRA[("mbarg", "inH2O")] = (0.401865, 0.0)
US |= {"kg": "lb", "mbarg": "inH2O", "GJ": "MMBtu"}


def pi_unit(unit: str, descriptor: str = "") -> str | None:
    """A PI point's engineering units as a US site's historian would name them (its values come from the process
    model, converted by Fuzer's synthetic provider): the stored unit, so a point's limits and values compare."""
    return target(unit)
# Columns holding a unit, and the value columns that unit applies to (any not in the file are skipped).
UNIT_COLUMNS = {
    "unit": ("result", "value", "low_limit", "high_limit", "low", "high", "volume", "thickness", "tmin", "nominal",
             "limit_value", "overall", "target", "setpoint"),
    "engunits": ("zero", "span", "typical"), "eng_units": ("pv_range_low", "pv_range_high"),
    "eu": ("setpoint", "value"), "uom": ("value", "HiHi", "Hi", "Lo", "LoLo", "low", "high"),
    "x_unit": ("x",), "y_unit": ("y",),
}
# Free-text columns and JSON keys whose quantities are converted where they are written with their unit.
TEXT_KEYS = {"text", "comment", "comments", "description", "long_text", "message", "notes", "note", "summary",
             "body", "content", "response", "damage_mechanism", "reason", "remarks", "work_done", "findings",
             "datum", "name", "title", "descriptor", "what", "subject", "details", "action", "cause", "question",
             "answer", "fact", "short_text", "work", "statement", "why", "expected", "story", "pattern"}
# Words an eval expects in an answer: the unit it should be given in.
MENTION = {"Sm3": "bbl", "Sm3/d": "bbl/d", "kSm3": "Mscf", "kSm3/d": "Mscf/d", "barg": "psig", "bar": "psi",
           "degC": "°F", "mm/s": "in/s", "mm": "in", "mm/y": "mpy"}
# Files kept as their source wrote them: internal models and drawings' answer keys in their own units.
SKIP = {"process_model.json", "instrument_faults.json", "pid_truth.json", "matches.json"}

_NUM = r"-?\d[\d,]*(?:\.\d+)?"
_UNITS_RE = "|".join(re.escape(u) for u in sorted(
    ["barg", "bara", "mbarg", "bar", "kPa", "MPa", "kSm3/d", "kSm3/day", "kSm3", "ksm3/d", "ksm3", "Sm3/d", "Sm3/day",
     "Sm3/h", "Sm3/Sm3", "Sm3", "sm3/d", "sm3", "MSm3", "m3/d", "m3/h", "Am3/h", "Nm3/h", "m3", "degC", "deg C", "°C",
     "DEG C", "mm/s", "mm/y", "mm/yr", "mm", "um", "µm", "kW", "MW", "km", "mg/Sm3", "kg/m3", "m"], key=len, reverse=True))
# "12.5 barg", "84-92 barg", "8 to 14 kSm3/d", "2450 mTVDss"; a unit glued to a word (mTVDss) is the only "m" taken
# without a space when followed by letters.
_QTY = re.compile(rf"(?<![\w.])({_NUM})(\s?(?:-|–|to)\s?({_NUM}))?(\s?)({_UNITS_RE})(?![\w/³])"
                  rf"|(?<![\w.])({_NUM})(\s?)(m)\s?(TVDss|TVD|MD|KB)\b")


def target(unit: str) -> str | None:
    """The US unit a metric unit is written in, or None for a unit already US (or unknown)."""
    u = (unit or "").strip()
    return US.get(u) or (US.get(u.lower()) if u.lower() in ("degc", "sm3/d", "ksm3/d", "mm") else None)


def convert(value: float, unit: str, to: str | None = None) -> float | None:
    """A metric value in its US unit (or `to`)."""
    to = to or target(unit)
    if not to:
        return None
    if (unit, to) in EXTRA:
        f, o = EXTRA[(unit, to)]
        return value * f + o
    if to == "MMscf/d":
        v, stored = units.normalise(value, unit)
        return v / 1000 if stored == "Mscf/d" else None
    v, stored = units.normalise(value, unit)
    if stored is None or stored == unit:
        return None
    if stored == to or (to, stored) in (("°F", "degF"),):
        return v
    back = units.convert(v, stored, to)
    return back


def _decimals(raw: str) -> int:
    s = raw.replace(",", "")
    return len(s.split(".")[1]) if "." in s else 0


def fmt(value: float, raw: str, factor: float) -> str:
    """`value` with the precision `raw` had, shifted by the conversion's scale (mm -> in gains digits)."""
    shift = -math.floor(math.log10(abs(factor))) if factor else 0
    d = max(0, _decimals(raw) + shift)
    if abs(value) >= 1000 and d > 0:
        d = min(d, 1)
    s = f"{value:.{d}f}"
    return "0" if s in ("-0", "-0.0") else s


def convert_raw(raw, unit: str, to: str | None = None) -> str | None:
    """A number as written (`"12.5"`, `"<0.5"`) in its US unit, written the same way; None when it isn't one."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip()
    m = re.fullmatch(r"([<>]=?|~)?\s*(-?\d[\d,]*(?:\.\d+)?(?:[eE]-?\d+)?)", text)
    if not m:
        return None
    x = float(m.group(2).replace(",", ""))
    v = convert(x, unit, to)
    if v is None:
        return None
    one = convert(1.0, unit, to)
    zero = convert(0.0, unit, to) or 0.0
    factor = (one - zero) if one is not None else 1.0
    if (to or target(unit)) == "API":
        factor = 0.1
    return (m.group(1) or "") + fmt(v, m.group(2), factor)


def text(s: str) -> str:
    """Quantities in running text in US units: "header at 74 barg" -> "header at 1073 psig"."""
    if not isinstance(s, str) or not s:
        return s

    def sub(m):
        if m.group(6):  # 2450 mTVDss
            v = convert_raw(m.group(6), "m")
            return f"{v} ft {m.group(9)}" if v else m.group(0)
        a, rng, b, space, u = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        unit = {"DEG C": "degC", "°C": "degC", "kSm3/day": "kSm3/d", "Sm3/day": "Sm3/d", "µm": "um",
                "MPa": "MPa"}.get(u, u)
        if unit == "MPa":
            to, conv = "psi", lambda r: _scaled(r, 145.038)
        else:
            to = target(unit)
            conv = lambda r: convert_raw(r, unit)
        if not to:
            return m.group(0)
        if unit == "degC" and _delta(s, m.end()):
            conv = lambda r: _scaled(r, 1.8)
        x = conv(a)
        if x is None:
            return m.group(0)
        shown = {"degF": "°F", "Mscf/d": "Mscf/d", "in/s": "in/s"}.get(to, to)
        if unit == "degC" and u in ("DEG C",):
            shown = "DEG F"
        if rng:
            y = conv(b)
            sep = rng[: len(rng) - len(b)]
            return f"{x}{sep}{y}{space}{shown}"
        return f"{x}{space}{shown}"

    return _QTY.sub(sub, s)


def _scaled(raw: str, factor: float) -> str:
    x = float(raw.replace(",", ""))
    return fmt(x * factor, raw, factor)


def _delta(s: str, end: int) -> bool:
    """A temperature difference: "9 degC hotter", "up about 9 degC", "25 degC wider", "60 degC or more under"."""
    after, before = s[end:end + 24].lower(), s[max(0, end - 40):end].lower()
    return bool(re.match(r"\s*(hotter|cooler|wider|narrower|above|below|over|under|or more|rise|drop|higher|lower|"
                         r"warmer|colder|from the others|approach|spread|difference)", after)
                or re.search(r"(up|down|by|rose|fell|risen|fallen|climbed|dropped)( about| roughly| some| nearly)? "
                             r"[\d.,]+\s?(degc|deg c|°c)$", before))


# Files whose rows mix units on purpose: rows already in a US unit go metric, so the mix survives.
SWAP_FILES = {"inspection_readings.csv", "lims_results.csv", "design_basis.csv", "dcs_alarms.csv",
              "dcs_alarm_journal.csv"}  # the older DCS on ST1 configured in psig stays the odd one out, in barg
METRIC = {"in": "mm", "degF": "degC", "bbl/d": "Sm3/d", "MMscf/d": "kSm3/d", "psig": "barg"}


def swap(r: dict) -> dict:
    """A row in a US unit in its metric twin (the odd rows of a mixed source)."""
    key = "unit" if "unit" in r else "eu"
    u = (r.get(key) or "").strip()
    to = METRIC.get(u)
    if not to:
        return r
    out = dict(r)
    for col in UNIT_COLUMNS[key]:
        if col == "nominal":  # a CML's nominal wall is written in mm whatever the survey's unit
            continue
        if col in r and (x := units.reading(r[col])) is not None:
            v = units.convert(x[0] * (1000 if u == "MMscf/d" else 1), "Mscf/d" if u == "MMscf/d" else u, to)
            if v is not None:
                one = units.convert(1.0, u, to) or 1.0
                out[col] = (x[1] or "") + fmt(v, str(r[col]).lstrip("<>=~ "), one if u != "degF" else 0.5556)
    out[key] = to
    return out


def _keep(x: float) -> float:
    """A converted number kept to about four significant figures (a small one too: 5.9e-06 psi a barrel)."""
    if abs(x) >= 1 or x == 0:
        return round(x, max(0, 4 - len(str(int(abs(x))))))
    return float(f"{x:.4g}")


def row(r: dict, swapped: bool = False) -> dict:
    """One record in US units: metric-named columns renamed and converted, values beside a unit column converted
    with it, quantities in free text converted. `swapped`: rows in a US unit go metric instead (SWAP_FILES)."""
    if swapped and (r.get("unit") or r.get("eu") or "").strip() in METRIC:
        return {k: text(v) if isinstance(v, str) and k.lower() in TEXT_KEYS else v for k, v in swap(r).items()}
    out = {}
    if "engunits" in r and "descriptor" in r:  # a PI point: its units only, values come from the historian
        return {k: (pi_unit(v, r["descriptor"]) or v) if k == "engunits" else v for k, v in r.items()}
    label = r.get("name") or r.get("descriptor") or r.get("description") or ""
    # unit column -> (metric unit, US unit)
    unit_cols = {k: (v.strip(), pi_unit(v, label) if k in ("uom", "engunits") else target(v))
                 for k, v in r.items() if k in UNIT_COLUMNS and isinstance(v, str) and target(v)}
    for k, v in r.items():
        if k in COLUMNS:
            name, unit = COLUMNS[k]
            if isinstance(v, dict):  # a value per well (a productivity index by well)
                out[name] = {w: round(convert(float(x), unit), 3) if isinstance(x, (int, float)) else x
                             for w, x in v.items()}
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                x = convert(float(v), unit)
                out[name] = (round(x, 3) if unit == "km" else _keep(x)) if x is not None else v  # a route meets its stations
            else:
                x = convert_raw(v, unit)
                out[name] = x if x is not None else (text(v) if isinstance(v, str) else v)
            continue
        if k in unit_cols:
            out[k] = unit_cols[k][1]
            continue
        hit = next((u for uk, u in unit_cols.items() if k in UNIT_COLUMNS[uk]), None)
        if hit is not None:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                x = convert(float(v), *hit)
                out[k] = _keep(x) if x is not None else v
            else:
                x = convert_raw(v, *hit)
                out[k] = x if x is not None else v
            continue
        if k == "peaks" and isinstance(v, str) and r.get("unit", "").startswith("mm/s"):
            out[k] = ";".join(f"{f}:{float(a) * 0.0393701:.4f}" for f, a in (p.split(":") for p in v.split(";") if p))
            continue
        if isinstance(v, str) and (k.lower() in TEXT_KEYS or k.lower().endswith(("_text", "text", "comment"))):
            out[k] = text(v)
            continue
        out[k] = v
    return out


def table(t: dict) -> dict:
    """A drawing's table (setpoints, equipment data) in US units: a row's numbers by its UNITS cell, and a column
    headed with a unit (`DES. P (barg)`) by that unit."""
    cols = [str(c) for c in t["columns"]]
    by_col = {i: m.group(1) for i, c in enumerate(cols) if (m := re.search(r"\((barg|DEG C|degC|mm|m3/h)\)", c))}
    unit_col = cols.index("UNITS") if "UNITS" in cols else None
    head = {"DEG C": "degC"}
    new_cols = [re.sub(r"\((barg|DEG C|degC|mm|m3/h)\)", lambda m: "(" + {"barg": "psig", "DEG C": "DEG F",
                       "degC": "DEG F", "mm": "in", "m3/h": "gpm"}[m.group(1)] + ")", c) for c in cols]
    rows = []
    for r in t["rows"]:
        cells = r.get("cells") if isinstance(r, dict) else None
        if not isinstance(cells, list):
            rows.append(r)
            continue
        unit = str(cells[unit_col]).strip() if unit_col is not None and unit_col < len(cells) else ""
        out = list(cells)
        for i, c in enumerate(cells):
            u = head.get(by_col.get(i), by_col.get(i)) or (unit if i != unit_col and target(unit) else None)
            if u and (x := convert_raw(c, u)) is not None:
                out[i] = x
        if unit_col is not None and target(unit):
            out[unit_col] = target(unit)
        rows.append({**r, "cells": out})
    return {**t, "columns": new_cols, "rows": rows}


def mentions(obj):
    """An eval's expected words (`must_mention`) with metric units as their US ones."""
    if isinstance(obj, list):
        return [mentions(x) for x in obj]
    return MENTION.get(obj, obj) if isinstance(obj, str) else obj


def value(obj):
    """A JSON document in US units, recursively (a dict as a record)."""
    if isinstance(obj, dict) and "must_mention" in obj:
        obj = {**obj, "must_mention": mentions(obj["must_mention"])}
    if isinstance(obj, list):
        return [value(x) for x in obj]
    if isinstance(obj, dict) and isinstance(obj.get("columns"), list) and isinstance(obj.get("rows"), list):
        obj = table(obj)
    if isinstance(obj, dict):
        return {k: value(v) if isinstance(v, (list, dict)) else v for k, v in row(obj).items()}
    return obj


# An OEM curve file's ratings carry no unit column: a flow is in the model's curve flow unit, a head in its head unit.
RATINGS = {("pump", "bep"): ("flow", "head"), ("pump", "mcsf"): ("flow", None), ("pump", "por"): ("flow", "flow"),
           ("pump", "aor"): ("flow", "flow"), ("pump", "impeller"): ("mm", None),
           ("gas_turbine", "fuel_lhv"): ("MJ/Sm3", None), ("gas_turbine", "iso_power"): ("kW", None),
           ("gas_turbine", "iso_heat_rate"): ("kJ/kWh", None)}


def _ratings(rows: list[dict]) -> list[dict]:
    """Each unit-less rating row in US units, read against its model's head curve units (before they convert)."""
    head = {(r["manufacturer"], r["model"]): (r["x_unit"], r["y_unit"]) for r in rows
            if r.get("series") == "head" and r.get("x_unit")}
    out = []
    for r in rows:
        spec = RATINGS.get((r.get("curve"), r.get("series"))) if not r.get("x_unit") else None
        if spec:
            flow, h = head.get((r["manufacturer"], r["model"]), (None, None))
            r = dict(r)
            for col, kind in zip(("x", "y"), spec):
                unit = {"flow": flow, "head": h}.get(kind, kind)
                if kind and unit and (v := convert_raw(r[col], unit)) is not None:
                    r[col] = v
        out.append(r)
    return out


def convert_csv(path: Path) -> None:
    raw = path.read_text()
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return
    if {"series", "x_unit", "model"} <= set(rows[0]):
        rows = _ratings(rows)
    out = [row(r, path.name in SWAP_FILES) for r in rows]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(out[0]), lineterminator="\r\n" if "\r\n" in raw else "\n")
    w.writeheader()
    w.writerows(out)
    path.write_text(buf.getvalue())


def convert_json(path: Path) -> None:
    raw = path.read_text()
    doc = json.loads(raw)
    new = value(doc)
    if new == doc:
        return
    lines = raw.splitlines()
    indent = (len(lines[1]) - len(lines[1].lstrip())) or None if len(lines) > 1 else None
    path.write_text(json.dumps(new, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))


def convert_dir(folder) -> list[str]:
    """Every export in a folder (and its subfolders) in US units, in place; the files changed."""
    changed = []
    for p in sorted(Path(folder).rglob("*")):
        if not p.is_file() or p.name in SKIP:
            continue
        before = p.read_bytes()
        if p.suffix == ".csv":
            convert_csv(p)
        elif p.suffix in (".json", ".geojson"):
            convert_json(p)
        elif p.suffix in (".md", ".txt"):
            p.write_text(text(p.read_text()))
        else:
            continue
        if p.read_bytes() != before:
            changed.append(str(p))
    return changed

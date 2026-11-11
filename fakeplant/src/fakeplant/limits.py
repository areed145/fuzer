"""Operating limits for the fake plant (#101): PI AF limit traits, a DCS / SCADA alarm export and an IOW
register, all set from the process model run without its stories (no trips, wear, fouling or shut-ins),
so the planted stories are what crosses them. A few are planted wrong or missing the way real ones are:
an AF value typed in psi, a DCS alarm moved without AF following, alarms disabled or set inside the
normal range, Hi above HiHi, points with no limits at all and an IOW on a tag that doesn't exist. The
answer key lists them."""

import random
import re
from datetime import datetime, timedelta

import numpy as np

from fuzer.sources.process_model import ProcessModel

# Healthy reference weeks: both seasons, both years (the wells' rates follow their tests).
WEEKS = ["2024-03-11", "2024-09-16", "2025-03-10", "2025-09-15"]
SKIP = {"choke", "position", "esdv", "wht", "glir", "liquid", "running", "load", "asv"}  # no limits: learned
WELL_LIMITS = {"whp": ("hihi", "hi", "lo"), "casp": ("hi", "lo")}
PSI = 14.5038
TAG = re.compile(r"(\d\d)-?([A-Z]+)-?(\d{4})([A-Z]?)\b")
DCS_TYPES = {"hihi": "PVHH", "hi": "PVHI", "lo": "PVLO", "lolo": "PVLL"}
PRIORITY = {"hihi": "1", "lolo": "1", "hi": "2", "lo": "2"}
# Points the stories run through: no planted faults on these.
STORY_UNITS = {"20-K-2300A", "20-E-2350A", "20-K-2200B", "20-V-2001", "20-V-2101C", "10-P-1001A", "10-P-1001B",
               "10-P-1001C", "header:20"}


def _healthy(spec: dict) -> dict[str, np.ndarray]:
    """Each modelled point's values over the reference weeks with no stories: only the operators' choke
    moves are kept, as normal operation. A standby pump is run as duty (in place of its group's first duty
    pump), so it gets running limits too."""
    out: dict[str, list] = {}

    def run(s, points):
        m = ProcessModel(s)
        for week in WEEKS:
            a = datetime.fromisoformat(week)
            b = a + timedelta(days=7)
            for p in points:
                out.setdefault(p, []).extend(v for _, v in m.values(p, a, b, 30))

    base = {**spec, "events": [e for e in spec["events"] if e["kind"] == "choke"]}
    standby = {u for u, x in spec["units"].items() if x.get("type") == "pump" and not x.get("duty", True)}
    run(base, [p for p, (u, _) in spec["points"].items() if u in spec["units"] and u not in standby])
    for u in standby:
        group = spec["units"][u]["group"]
        duty = next(p for p, x in spec["units"].items() if x.get("type") == "pump" and x.get("group") == group
                    and x.get("duty", True))
        units = {**spec["units"], u: {**spec["units"][u], "duty": True}, duty: {**spec["units"][duty], "duty": False}}
        run({**base, "units": units}, [p for p, (unit, _) in spec["points"].items() if unit == u])
    return {p: np.array(v) for p, v in out.items()}


def _round(v: float) -> float:
    return round(v) if abs(v) >= 100 else round(v, 1)


def design(var: str, v: np.ndarray, well: bool) -> dict[str, float]:
    """Alarm limits for one point from its healthy values, the way a process engineer would set them."""
    lo, med, hi = (float(x) for x in np.percentile(v, [0.5, 50, 99.5]))
    r = max(hi - lo, 0.03 * abs(med), 0.2)
    if well:
        out = {"hihi": hi + max(r, 0.15 * med), "hi": hi + max(0.5 * r, 0.08 * med), "lo": lo - max(0.5 * r, 0.1 * med)}
        return {k: _round(x) for k, x in out.items() if k in WELL_LIMITS[var] and x > 0}
    if var == "vibration":
        return {"hihi": 7.1, "hi": 4.5}  # ISO 10816-3 zones C and D
    if var == "liquid_level":
        return {"hihi": _round(min(95, med + 37)), "hi": _round(med + 27), "lo": _round(max(8, med - 25)),
                "lolo": _round(max(3, med - 32))}
    rise = {"discharge_temperature": (10, 28), "bearing_temperature": (12, 22), "exhaust_temperature": (25, 45),
            "outlet_temperature": (10, 20)}
    if var in rise:
        return {"hihi": _round(hi + rise[var][1]), "hi": _round(hi + rise[var][0])}
    if var == "speed":
        return {"hihi": _round(hi * 1.08), "hi": _round(hi * 1.05)}
    out = {"hihi": hi + max(r, 0.10 * abs(med)), "hi": hi + max(0.5 * r, 0.05 * abs(med)),
           "lo": lo - max(0.5 * r, 0.05 * abs(med)), "lolo": lo - max(r, 0.10 * abs(med))}
    return {k: _round(x) for k, x in out.items() if x > 0}


def okan_limits(spec: dict, points: list[dict], af: list[dict], seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Adds AF limit traits to `af` in place; returns the DCS alarm export, the IOW register and the
    answer key of planted faults."""
    rng = random.Random(seed + 21)
    healthy = _healthy(spec)
    uom = {p["tag"]: p["engunits"] for p in points}
    units = spec["units"]
    limits: dict[str, dict[str, float]] = {}
    for p, (u, var) in spec["points"].items():
        well = units.get(u, {}).get("type") == "well"
        if p not in healthy or var in SKIP or (well and var not in WELL_LIMITS):
            continue
        limits[p] = design(var, healthy[p], well)

    # Points added later (#474) get limits of their own but aren't where the planted faults
    # go, so the faults stay on the points they were planted on.
    def came_later(p):  # compressor flows, gas turbine fuel and output, the ambient temperature (#474)
        u, var = spec["points"][p]
        kind = units.get(u, {}).get("type")
        return (kind, var) in {("compressor", "flow"), ("gas_turbine", "fuel_flow"), ("gas_turbine", "power"),
                               ("weather", "ambient")}
    later = {p: limits.pop(p) for p in list(limits) if came_later(p)}
    af_points = {a["pi_point"]: a for el in af for a in el["attributes"]}
    story = {p for p, (u, _) in spec["points"].items() if u in STORY_UNITS}
    plant = sorted(p for p in limits if p in af_points and p not in story)  # faults go on equipment points
    rng.shuffle(plant)
    truth: list[dict] = []
    faults = {"missing": plant[:4], "af_psi": [p for p in plant[4:12] if uom[p] == "barg"][:2],
              "moved": plant[12:18], "disabled": plant[18:21], "inverted": plant[21:23], "standing": plant[23:25]}

    for p in faults["missing"]:
        del limits[p]
        truth.append({"point": p, "fault": "missing", "detail": "no alarm limits in AF or the DCS"})
    for p in sorted(set(af_points) - set(spec["points"])):  # never modelled, so never given limits
        truth.append({"point": p, "fault": "missing", "detail": "no alarm limits in AF or the DCS"})
    dcs = {p: dict(v) for p, v in limits.items()}
    af_lim = {p: dict(v) for p, v in limits.items() if p in af_points}
    for p in rng.sample(sorted(af_lim), len(af_lim) // 10):  # AF modelled late: limits never copied in
        if p not in story:
            del af_lim[p]
    for p in faults["af_psi"]:
        if p in af_lim and "hi" in af_lim[p]:
            af_lim[p]["hi"] = _round(af_lim[p]["hi"] * PSI)
            truth.append({"point": p, "fault": "wrong_unit", "source": "pi_af", "type": "hi",
                          "detail": "AF Hi typed in psi against a barg attribute"})
    changed = {}
    for p in faults["moved"]:
        if "hi" in dcs[p]:
            dcs[p]["hi"] = _round((dcs[p]["hi"] + dcs[p]["hihi"]) / 2 if "hihi" in dcs[p] else dcs[p]["hi"] * 1.1)
            changed[p] = f"2025-{rng.randint(2, 10):02d}-{rng.randint(1, 28):02d}"
            if p in af_lim:
                truth.append({"point": p, "fault": "disagree", "type": "hi",
                              "detail": "DCS Hi moved by an MOC; AF not updated"})
    disabled = set()
    for p in faults["disabled"]:
        if "hi" in dcs[p]:
            disabled.add(p)
            truth.append({"point": p, "fault": "disabled", "source": "dcs", "type": "hi", "detail": "DCS Hi alarm disabled"})
    for p in faults["inverted"]:
        if "hi" in dcs[p] and "hihi" in dcs[p]:
            dcs[p]["hi"], dcs[p]["hihi"] = dcs[p]["hihi"], dcs[p]["hi"]
            truth.append({"point": p, "fault": "order", "source": "dcs", "type": "hi", "detail": "DCS Hi above HiHi"})
    for p in faults["standing"]:
        if "lo" in dcs[p]:
            dcs[p]["lo"] = _round(float(np.percentile(healthy[p], 60)))
            truth.append({"point": p, "fault": "standing", "source": "dcs", "type": "lo",
                          "detail": "DCS Lo set inside the normal range"})

    dcs |= {p: dict(v) for p, v in later.items()}
    af_lim |= {p: dict(v) for p, v in later.items() if p in af_points}
    for p, a in af_points.items():  # AF limit traits, as attributes of the attribute
        for t, v in af_lim.get(p, {}).items():
            a[{"hihi": "HiHi", "hi": "Hi", "lo": "Lo", "lolo": "LoLo"}[t]] = v

    rows = []
    for p in sorted(dcs):
        u, var = spec["points"][p]
        well = units.get(u, {}).get("type") == "well"
        # The DCS names a point by its compact tag (20PT2300A); well RTUs by the SCADA point itself. ST1's
        # older DCS is configured in psig.
        tag = p if well else "".join(TAG.search(p).groups())
        psig = tag.startswith("30") and uom[p] == "barg"
        for t, v in dcs[p].items():
            rows.append({"tag": tag, "alarm": DCS_TYPES[t], "setpoint": _round(v * PSI) if psig else v,
                         "eu": "psig" if psig else uom[p], "priority": PRIORITY[t],
                         "enabled": "N" if p in disabled and t == "hi" else "Y",
                         "last_changed": changed.get(p, "2019-05-14") if t == "hi" else "2019-05-14"})

    iows = _iows(spec, healthy)
    truth.append({"point": None, "tag": "20-TT-2310B", "fault": "unresolved", "source": "iow",
                  "detail": "IOW-010 names a tag that doesn't exist (20-TT-2300B meant)"})
    return rows, iows, truth


def _iows(spec: dict, healthy: dict) -> list[dict]:
    """The integrity operating windows (API RP 584): compression discharge and after-cooler temperatures,
    glycol contactor pressure, separator pressure, lift gas header pressure."""
    of = {(u, v): p for p, (u, v) in spec["points"].items()}

    def top(u, v, add):
        return _round(float(np.percentile(healthy[of[(u, v)]], 99.5)) + add)

    def bottom(u, v, less):
        return _round(float(np.percentile(healthy[of[(u, v)]], 0.5)) - less)

    rows = []

    def iow(n, rev, tag, parameter, cls, low, high, unit, allowed, mechanism, response, eff, sup=""):
        rows.append({"iow_id": f"IOW-{n:03d}", "revision": rev, "tag": tag, "parameter": parameter, "iow_class": cls,
                     "low_limit": low, "high_limit": high, "unit": unit, "max_time_outside": allowed,
                     "damage_mechanism": mechanism, "response": response, "effective_date": eff, "superseded_date": sup})

    for n, t in enumerate("ABC", 1):
        high = top(f"20-K-2300{t}", "discharge_temperature", 20)
        if t == "A":  # rev A was looser; tightened after the 2024 trips
            iow(n, "A", f"20-TT-2300{t}", "3rd stage discharge temperature", "Critical", "", high + 6, "degC", "4 hrs",
                "Discharge valve and dry gas seal damage, lube oil coking", "Cut load, open recycle, clean the after-cooler",
                "2021-03-01", "2025-01-01")
            iow(n, "B", f"20-TT-2300{t}", "3rd stage discharge temperature", "Critical", "", high, "degC", "2 h",
                "Discharge valve and dry gas seal damage, lube oil coking", "Cut load, open recycle, clean the after-cooler",
                "2025-01-01")
        else:
            iow(n, "A", f"20-TT-2300{t}", "3rd stage discharge temperature", "Critical", "", high, "degC", "2 h",
                "Discharge valve and dry gas seal damage, lube oil coking", "Cut load, open recycle, clean the after-cooler",
                "2021-03-01")
    for n, t in enumerate("ABC", 4):
        iow(n, "A", f"20-TT-2350{t}", "3rd stage after-cooler outlet temperature", "Standard", "",
            top(f"20-E-2350{t}", "outlet_temperature", 15), "degC", "24 hours",
            "Glycol degradation and losses in the contactor downstream", "Clean the after-cooler; check cooling water",
            "2021-03-01")
    iow(7, "A", "20-PT-2400", "Glycol contactor pressure", "Standard", bottom("20-V-2400", "vessel_pressure", 6), "",
        "barg", "12 h", "Dew point margin lost: free water, hydrates and corrosion in the export line",
        "Restore contactor pressure or cut throughput", "2021-03-01")
    iow(8, "A", "10-PT-2001", "HP separator pressure", "Informational", bottom("10-V-2001", "vessel_pressure", 2),
        top("10-V-2001", "vessel_pressure", 2), "barg", "", "Carry-under and erosion at the outlet nozzles",
        "Note in the shift log", "2021-03-01")
    iow(9, "A", "20-PT-3003", "Lift gas header pressure", "Informational", 75, "", "barg", "",
        "Hydrate formation in jacket gas lift lines at low header pressure", "Note in the shift log", "2021-03-01")
    iow(10, "A", "20-TT-2310B", "3rd stage discharge temperature", "Critical", "",
        top("20-K-2300B", "discharge_temperature", 20), "degC", "120 min",
        "Discharge valve and dry gas seal damage, lube oil coking", "Cut load, open recycle", "2021-03-01")
    return rows

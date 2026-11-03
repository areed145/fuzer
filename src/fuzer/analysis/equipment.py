"""Equipment performance (#105 B): compressors, coolers and peers, as calcs ({value, expected, flag,
basis, sources}) for detectors, agents and charts.

- `compressor_performance(stage)`: polytropic efficiency and head of one compressor stage over time, from
  its suction (the scrubber before it, and the cooler before that) and discharge pressure and
  temperature: eta = ((k - 1) / k) ln(Pd / Ps) / ln(Td / Ts). A falling efficiency means fouling, wear or
  recycle through a hot cooler. There is no flow or OEM map in the data, so no distance to surge; it says so.
- `cooler_fouling(cooler)`: its outlet temperature against its clean baseline (the coolest days in the
  window), the rise since it was last cleaned, and the date it reaches its high limit (#101) at that rate.
- `peers(ref)`: the units of one equipment class (or one model) side by side: failures per year, MTBF,
  oil lost, and vibration now. Outliers are the ones far from the class's median (a bad unit);
  `class_wide_failures` when the class's median unit fails 1.5 times a year or more (the model or its
  service, not one unit).
"""

import math
import re
from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of

CLASS_WIDE = 1.5  # failures per year at the class median: a class-wide problem
K = 1.3  # ratio of specific heats for the lift gas
R_GAS = 0.5  # kJ/kg K (natural gas, molar mass about 16.6)


def _tag_point(conn, label: str, ending: str = "") -> dict | None:
    from ..sources.timeseries import _unit_points

    t = conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()
    if not t:
        return None
    pts = _unit_points(conn, t["fuzer_id"], 10)
    return next((p for p in pts if ending.lower() in (p["description"] or "").lower()), pts[0] if pts and not ending else None)


def _stage_points(conn, stage_label: str) -> dict:
    """Suction and discharge pressure and temperature points for a compressor stage named like 20-K-2300A:
    discharge on the stage itself, suction pressure on its scrubber (20-V-2301A), suction temperature at the
    outlet of the cooler before it (20-E-2250A)."""
    m = re.match(r"^(\d\d)-K-(\d)(\d)00([A-Z])$", stage_label)
    if not m:
        raise CalcError("not a compressor stage tag (NN-K-N?00X)")
    area, series, n, train = m.groups()  # 20-K-2300A: series 2, stage 3, train A
    s = int(n)
    out = {"pd": _tag_point(conn, stage_label, "Discharge Pressure"),
           "td": _tag_point(conn, stage_label, "Discharge Temperature"),
           "ps": _tag_point(conn, f"{area}-V-{series}{s}01{train}", "Pressure"),
           "ts": _tag_point(conn, f"{area}-E-{series}{s - 1}50{train}", "Temperature") if s > 1 else None}
    for k in ("pd", "td", "ps"):
        if not out[k]:
            raise CalcError(f"no {k} point for that stage")
    return out


def compressor_performance(conn, fid: str, days: int = 30, until: str | None = None, src=None,
                           drop: float = 0.03) -> dict:
    """Polytropic efficiency (and head) of a compressor stage: the last 3 days against its first week in
    the window. Flags `efficiency_drop` when it is `drop` or more lower."""
    label = _label(conn, fid)
    pts = _stage_points(conn, label)
    start, end = window_of(conn, days, until)
    v = read({k: p for k, p in pts.items() if p}, start, end, 60, src)
    rows = []
    for t in sorted(v["pd"]):
        ps, pd_, td = v["ps"].get(t), v["pd"][t], v["td"].get(t)
        ts = v["ts"].get(t) if pts["ts"] else None
        if ps is None or td is None or ts is None or pd_ / max(ps, 0.1) < 1.3:
            continue  # stopped or recycling: the ratio says nothing
        pr, tr = (pd_ + 1.013) / (ps + 1.013), (td + 273.15) / (ts + 273.15)
        if tr <= 1.0:
            continue
        eta = (K - 1) / K * math.log(pr) / math.log(tr)
        n = 1 / (1 - (K - 1) / (K * eta))  # polytropic exponent
        head = R_GAS * (ts + 273.15) * n / (n - 1) * (pr ** ((n - 1) / n) - 1)
        rows.append((t, eta, head))
    if len(rows) < 48:
        if not pts["ts"]:
            raise CalcError("no suction temperature for the first stage (no cooler before it)")
        raise CalcError("the stage hardly ran in that window")
    first = [r for r in rows if r[0] < (start + timedelta(days=7)).isoformat()] or rows[:72]
    last = [r for r in rows if r[0] >= (end - timedelta(days=3)).isoformat()] or rows[-72:]
    base, now = float(np.median([r[1] for r in first])), float(np.median([r[1] for r in last]))
    daily: dict[str, list[float]] = {}
    for t, e, _ in rows:
        daily.setdefault(t[:10], []).append(e)
    return result(round(now, 3), round(base, 3), "efficiency_drop" if now <= base - drop else None,
                  f"polytropic efficiency ((k-1)/k) ln(Pd/Ps) / ln(Td/Ts), k {K}, hourly while running; the last 3 days "
                  f"against the window's first week", [fid, *[p["id"] for p in pts.values() if p]], unit="polytropic efficiency",
                  head_kj_kg=round(float(np.median([r[2] for r in last])), 1),
                  daily=[{"day": d, "efficiency": round(float(np.median(x)), 3)} for d, x in sorted(daily.items())],
                  note="no flow measurement or OEM compressor map in the data: no distance to surge")


def cooler_fouling(conn, fid: str, days: int = 180, until: str | None = None, src=None, pack=None,
                   rise: float = 5.0) -> dict:
    """A cooler's outlet temperature against its clean baseline: degrees above it now, the rate since it was
    last cleaned and when it reaches its high limit at that rate. Flags `fouling` at `rise` degrees or more."""
    from . import limits, signals

    label = _label(conn, fid)
    p = _tag_point(conn, label.replace("-E-", "-TT-"), "Temperature") or _tag_point(conn, label, "Temperature")
    if not p:
        raise CalcError("no outlet temperature point on that cooler")
    start, end = window_of(conn, days, until)
    v = read({"t": p}, start, end, 60, src)["t"]
    daily: dict[str, list[float]] = {}
    for t, x in v.items():
        daily.setdefault(t[:10], []).append(x)
    days_ = sorted(daily)
    med = np.array([np.median(daily[d]) for d in days_])  # daily medians: the day's swing doesn't count
    if len(med) < 14:
        raise CalcError("fewer than 14 days of outlet temperature")
    clean = float(np.percentile(med, 10))
    now = float(np.median(med[-7:]))
    times = [datetime.fromisoformat(d) for d in days_]
    found = signals.detect_changes(times, med, 1440, window_minutes=1440 * 3)
    since = max((c["start"] for c in found if c["kind"] in ("step", "variance") and c["after"] < c["before"]),
                default=days_[0])  # the last clean: a drop
    seg = [(i, m) for i, (d, m) in enumerate(zip(days_, med)) if d >= since[:10]]
    slope = float(np.polyfit([i for i, _ in seg], [m for _, m in seg], 1)[0]) if len(seg) >= 5 else 0.0
    lines = limits.lines(limits.for_points(conn, pack, [p["id"]], end)[p["id"]])
    hi = min((x["value"] for x in lines if x["type"] in ("hi", "hihi")), default=None)
    reach = None
    if hi is not None and now >= hi:
        reach = "already past it"
    elif hi is not None and slope > 0.01:
        reach = (end + timedelta(days=(hi - now) / slope)).date().isoformat()
    return result(round(now, 1), round(clean, 1), "fouling" if now - clean >= rise and slope > 0 else None,
                  f"daily median outlet temperature; expected is the window's coolest tenth of days (clean); the rate "
                  f"is since the last drop (a clean), {since[:10]}", [fid, p["id"]], unit="degC",
                  rise_per_day=round(slope, 3), since_clean=since[:10], high_limit=hi, reaches_limit=reach,
                  daily=[{"day": d, "temp": round(float(m), 2)} for d, m in zip(days_, med)][-120:])


def _class_of(conn, fid: str) -> str:
    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind and kind["kind"] in ("equipment_class", "equipment_model"):
        return fid
    c = conn.execute("select dst from edge where rel = 'classified_as' and src = %s limit 1", (fid,)).fetchone()
    if not c:
        raise CalcError("no equipment class for that unit")
    return c["dst"]


def peers(conn, fid: str, z: float = 3.0, src=None) -> dict:
    """The units of a class (or model, or a unit's class) side by side: failures per year, MTBF, oil lost
    and vibration now; outliers far from the class's median (robust z of `z` or more)."""
    from ..graph import operations
    from ..sources.timeseries import _unit_points

    cls = _class_of(conn, fid)
    rel = "instance_of" if conn.execute("select kind from entity where fuzer_id = %s", (cls,)).fetchone()["kind"] == \
        "equipment_model" else "classified_as"
    members = [r["src"] for r in conn.execute("select distinct src from edge e join entity t on t.fuzer_id = e.src "
                                              "and t.kind = 'tag' where e.rel = %s and e.dst = %s", (rel, cls))]
    if len(members) < 3:
        raise CalcError("fewer than three units to compare")
    b = operations.bad_actors(conn, limit=100000)
    ba = {a["id"]: a for a in b["bad_actors"]}
    years = max((datetime.fromisoformat(b["until"]) - datetime.fromisoformat(b["since"])).days / 365.25, 0.1)
    end = window_of(conn, 1)[1]
    rows = []
    for m in members:
        a = ba.get(m, {})
        vib = next((p for p in _unit_points(conn, m, 10) if (p["uom"] or "") == "mm/s"), None)
        v = None
        if vib:
            x = read({"v": vib}, end - timedelta(days=7), end, 360, src)["v"]
            v = round(float(np.median(list(x.values()))), 2) if x else None
        rows.append({"tag": _label(conn, m), "id": m, "failures_per_year": round(a.get("failures", 0) / years, 2),
                     "mtbf_days": a.get("mtbf_days"), "lost_oil_sm3": a.get("lost_oil_sm3", 0), "vibration_mm_s": v})

    def robust_z(key):
        xs = np.array([r[key] for r in rows if r[key] is not None], float)
        if len(xs) < 3:
            return
        med = np.median(xs)
        mad = 1.4826 * np.median(np.abs(xs - med)) or (np.std(xs) or 1.0)
        for r in rows:
            if r[key] is not None:
                r[f"{key}_z"] = round(float((r[key] - med) / mad), 1)

    for key in ("failures_per_year", "vibration_mm_s"):
        robust_z(key)
    out = [r for r in rows if any(abs(r.get(f"{k}_z") or 0) >= z for k in ("failures_per_year", "vibration_mm_s"))]
    fpy = sorted(r["failures_per_year"] for r in rows)
    wide = fpy[len(fpy) // 2] >= CLASS_WIDE  # most of the class fails often: the model or its service, not one unit
    return result(len(out), 0, "outliers" if out else "class_wide_failures" if wide else None,
                  f"SAP failure notifications over {years:.1f} years and vibration over the last 7 days for the "
                  f"{len(rows)} units of {_label(conn, cls)}; robust z (median and MAD) of {z} or more",
                  [cls, *[r["id"] for r in out]], unit="outlying units", class_name=_label(conn, cls),
                  class_median_failures_per_year=fpy[len(fpy) // 2], outliers=out,
                  units=sorted(rows, key=lambda r: -r["failures_per_year"])[:30])

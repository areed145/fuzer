"""Compressor performance on real-gas properties, and the train's power balance.

- `gas_properties(ref)`: the gas the stage compresses: relative density and molar mass from the latest gas analysis
  (LIMS, a GC to GPA 2261) on the train or its inlet, else a stated default. Compressibility Z by Papay from the
  Sutton pseudo-critical properties, and the ideal-gas ratio of specific heats k from the relative density and
  temperature, so k follows the gas rather than a fixed 1.3.
- `gas_performance(ref)`: a centrifugal stage's polytropic efficiency, head and absorbed gas power, hourly while it
  runs, on those properties (ASME PTC 10 / ISO 5389 on field data: the Schultz method with X ~ 0 and Y ~ 1, which
  holds at these reduced conditions). The last 3 days against the window's first week. Flags `efficiency_drop` at 3
  points or more lower, like the ideal-gas `compressor_performance`, whose fixed k it reports beside it.
- `power_balance(ref)`: a gas turbine driven train's power balance: the gas power its stages absorb (mass flow x
  polytropic head / efficiency) against the driver's output power. The ratio is a closure, about the mechanical and
  gearbox efficiency; it shifting means a flow meter, a power estimate or a stage efficiency has moved. Flags
  `power_mismatch` when the last 3 days' closure is 8 % or more off the window's first week.
"""

import math
import re
from datetime import timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of

R_U = 8.314462  # J/(mol K)
AIR = 28.9647  # g/mol
DEFAULT_SG = 0.68  # a lean associated gas, when no analysis is on file
DROP = 0.03
MISMATCH = 0.08


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def gas_properties(conn, fid: str | None = None, until: str | None = None) -> dict:
    """The latest relative density and molar mass by gas analysis: on the train's units first, else anywhere."""
    near: list[str] = []
    if fid:
        try:
            from .trips import train

            tr = train(conn, _tag(conn, fid))
            near = tr["members"] + tr["shared"]
        except CalcError:
            near = []
    found = {}
    for q in ("relative_density", "molar_mass"):
        r = conn.execute(
            "select value, at, fuzer_id, record from measurement where quantity = %s and value is not null "
            "and (%s::timestamptz is null or at <= %s::timestamptz) "
            "order by (fuzer_id = any(%s)) desc, at desc limit 1", (q, until, until, near)).fetchone()
        if r:
            found[q] = r
    if "relative_density" in found:
        sg = float(found["relative_density"]["value"])
        src = found["relative_density"]
    elif "molar_mass" in found:
        sg = float(found["molar_mass"]["value"]) / AIR
        src = found["molar_mass"]
    else:
        sg, src = DEFAULT_SG, None
    mw = float(found["molar_mass"]["value"]) if "molar_mass" in found else sg * AIR
    tpc = (169.2 + 349.5 * sg - 74.0 * sg * sg) / 1.8  # K (Sutton)
    ppc = (756.8 - 131.0 * sg - 3.6 * sg * sg) * 0.0689476  # bar a
    return {"relative_density": round(sg, 4), "molar_mass": round(mw, 2), "tpc_k": round(tpc, 1), "ppc_bara": round(ppc, 2),
            "from": "gas analysis" if src else f"default (no gas analysis on file; relative density {DEFAULT_SG})",
            "sampled_at": src["at"].isoformat() if src else None, "source": src["record"] if src else None}


def z_factor(g: dict, p_bara: float, t_k: float) -> float:
    """Compressibility by Papay at the gas's pseudo-reduced pressure and temperature."""
    pr, tr = p_bara / g["ppc_bara"], t_k / g["tpc_k"]
    return 1 - 3.52 * pr / 10 ** (0.9813 * tr) + 0.274 * pr * pr / 10 ** (0.8157 * tr)


def k_ideal(g: dict, t_k: float) -> float:
    """The ideal-gas ratio of specific heats from the relative density and temperature (a methane-rich gas)."""
    cp = 35.7 + 22.0 * (g["relative_density"] - 0.554) + 0.045 * (t_k - 300)  # J/(mol K)
    return cp / (cp - R_U)


def stage_state(g: dict, ps: float, pd: float, ts: float, td: float) -> dict | None:
    """Polytropic efficiency, head (kJ/kg) and the suction density on real-gas properties, from gauge pressures
    (barg) and temperatures (degC). None when the stage isn't compressing."""
    p1, p2, t1, t2 = ps + 1.01325, pd + 1.01325, ts + 273.15, td + 273.15
    if p2 / p1 < 1.2 or t2 <= t1:
        return None
    z1, z2 = z_factor(g, p1, t1), z_factor(g, p2, t2)
    k = k_ideal(g, (t1 + t2) / 2)
    m = math.log(t2 / t1) / math.log(p2 / p1)  # temperature exponent
    eta = (k - 1) / k / m
    if not 0.3 < eta < 1.0:
        return None
    n = math.log(p2 / p1) / math.log((p2 / p1) * (z1 * t1) / (z2 * t2))  # volume exponent: P v^n constant
    r = R_U / g["molar_mass"]  # kJ/(kg K)
    head = n / (n - 1) * (z1 + z2) / 2 * r * t1 * ((p2 / p1) ** ((n - 1) / n) - 1)
    rho1 = p1 * 1e5 * g["molar_mass"] / 1000 / (z1 * R_U * t1)  # kg/m3
    return {"eta": eta, "head": head, "rho1": rho1, "k": k, "z1": z1, "z2": z2, "n": n}


def _stage(conn, label: str) -> dict:
    """A stage's points: pressures and temperatures (the cooler before it, else the ambient + 8 for a 1st stage
    with no suction thermometer), and its suction flow."""
    from ..graph import roles
    from .equipment import _stage_points, role_point

    stage = conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]
    pts = dict(_stage_points(conn, stage))
    flow = role_point(conn, stage, "suction_flow")
    if flow:
        pts["q"] = flow
    assumed = None
    if not pts.get("ts"):
        if amb := roles.anywhere(conn, "ambient_temperature"):
            from ..sources.timeseries import _point

            pts["ts"] = _point(conn, amb)
            assumed = "the ambient temperature + 8 degC (no suction thermometer on a 1st stage)"
    missing = [k for k in ("ps", "pd", "ts", "td") if not pts.get(k)]
    if missing:
        raise CalcError(f"{label}: no " + ", ".join(missing) + " point")
    return {"points": pts, "assumed_suction": assumed}


def _rows(g: dict, st: dict, v: dict, ts: str) -> dict | None:
    x = {k: v[k].get(ts) for k in st["points"]}
    if any(x[k] is None for k in ("ps", "pd", "ts", "td")):
        return None
    t_s = x["ts"] + 8 if st["assumed_suction"] else x["ts"]
    s = stage_state(g, x["ps"], x["pd"], t_s, x["td"])
    if s and x.get("q"):
        s["mass_kg_s"] = x["q"] * s["rho1"] / 3600
        s["gas_kw"] = s["mass_kg_s"] * s["head"] / s["eta"]
    return s


def gas_performance(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """A centrifugal stage's polytropic efficiency, head and gas power on real-gas properties, now against the
    window's first week."""
    from . import equipment

    tag = _tag(conn, fid)
    label = _label(conn, tag)
    if not re.match(r"^\d\d-K-\d\d00[A-Z]$", label):
        raise CalcError("not a centrifugal compressor stage tag (NN-K-N?00X)")
    st = _stage(conn, label)
    g = gas_properties(conn, tag, until)
    start, end = window_of(conn, days, until)
    v = read(st["points"], start, end, 60, src)
    rows = [(t, s) for t in sorted(v["pd"]) if (s := _rows(g, st, v, t))]
    if len(rows) < 48:
        raise CalcError("the stage hardly ran in that window")
    first = [s for t, s in rows if t < (start + timedelta(days=7)).isoformat()] or [s for _, s in rows[:72]]
    last = [s for t, s in rows if t >= (end - timedelta(days=3)).isoformat()] or [s for _, s in rows[-72:]]
    med = lambda xs, k: float(np.median([x[k] for x in xs if k in x])) if any(k in x for x in xs) else None
    base, now = med(first, "eta"), med(last, "eta")
    ideal = None
    try:
        ideal = equipment.compressor_performance(conn, tag, days=days, until=until, src=src)["value"]
    except CalcError:
        pass
    daily: dict[str, list[float]] = {}
    for t, s in rows:
        daily.setdefault(t[:10], []).append(s["eta"])
    return result(round(now, 3), round(base, 3), "efficiency_drop" if now <= base - DROP else None,
                  "polytropic efficiency and head on real-gas properties (ASME PTC 10 / ISO 5389 on field data, "
                  "Schultz with X ~ 0, Y ~ 1): k from the gas analysis's relative density at the mean temperature, Z by "
                  "Papay from Sutton pseudo-criticals, the volume exponent from P, T and Z at both ends; hourly while "
                  "running; the last 3 days against the window's first week"
                  + (f"; suction temperature taken as {st['assumed_suction']}" if st["assumed_suction"] else ""),
                  [tag, *[p["id"] for p in st["points"].values()], *([g["source"]] if g["source"] else [])],
                  unit="polytropic efficiency", machine=label, gas=g,
                  head_kj_kg=round(med(last, "head"), 1), k=round(med(last, "k"), 3),
                  z_suction=round(med(last, "z1"), 3), z_discharge=round(med(last, "z2"), 3),
                  mass_flow_kg_s=round(m, 2) if (m := med(last, "mass_kg_s")) else None,
                  gas_power_kw=round(p, 0) if (p := med(last, "gas_kw")) else None,
                  ideal_gas_efficiency=ideal, data_from=rows[0][0][:10], data_to=rows[-1][0][:10], readings=len(rows),
                  daily=[{"day": d, "efficiency": round(float(np.median(x)), 3)} for d, x in sorted(daily.items())][-60:])


def power_balance(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """The gas power a train's stages absorb against its gas turbine's output power: the closure now against the
    window's first week."""
    from ..graph import roles
    from .equipment import role_point
    from .trips import train

    tag = _tag(conn, fid)
    tr = train(conn, tag)
    members = sorted(tr["members"], key=lambda m: _label(conn, m))
    gt = next((m for m in members if "GT" in roles.classes(conn, m)), None)
    stages = [_label(conn, m) for m in members if "CO" in roles.classes(conn, m)]
    if not gt or not stages:
        raise CalcError("no gas turbine driven centrifugal train for that machine")
    power = role_point(conn, gt, "power")
    if not power:
        raise CalcError(f"no output power point on {_label(conn, gt)}")
    g = gas_properties(conn, tag, until)
    sts = {s: _stage(conn, s) for s in stages}
    start, end = window_of(conn, days, until)
    vals = {s: read(st["points"], start, end, 60, src) for s, st in sts.items()}
    pw = read({"kw": power}, start, end, 60, src)["kw"]
    top = float(np.percentile(list(pw.values()), 90)) if pw else 0.0
    rows = []
    for t in sorted(pw):
        if pw[t] < 0.5 * top:
            continue
        parts = [_rows(g, sts[s], vals[s], t) for s in stages]
        if all(p and "gas_kw" in p for p in parts):
            rows.append((t, sum(p["gas_kw"] for p in parts), pw[t], [p["gas_kw"] for p in parts]))
    if len(rows) < 48:
        raise CalcError("the train hardly ran with every stage measurable in that window")
    first = [r for r in rows if r[0] < (start + timedelta(days=7)).isoformat()] or rows[:72]
    last = [r for r in rows if r[0] >= (end - timedelta(days=3)).isoformat()] or rows[-72:]
    close = lambda rs: float(np.median([r[1] / r[2] for r in rs]))
    base, now = close(first), close(last)
    shift = now / base - 1
    return result(round(now, 3), round(base, 3), "power_mismatch" if abs(shift) >= MISMATCH else None,
                  f"gas power absorbed by {len(stages)} stage(s) (mass flow from the suction flow at real-gas density x "
                  f"polytropic head / efficiency) over {gt}'s output power, hourly above half load; the last 3 days "
                  f"against the window's first week. About the mechanical and gearbox efficiency when the meters and "
                  f"the power estimate are right; a shift of {MISMATCH * 100:g} % or more means one of them moved",
                  [tag, power["id"], *[p["id"] for st in sts.values() for p in st["points"].values()]],
                  unit="closure (gas power / driver power)", machine=_label(conn, tag), driver=gt, train=tr["name"],
                  shift_pct=round(shift * 100, 1), gas_power_kw=round(float(np.median([r[1] for r in last])), 0),
                  driver_power_kw=round(float(np.median([r[2] for r in last])), 0),
                  by_stage=[{"stage": s, "gas_power_kw": round(float(np.median([r[3][i] for r in last])), 0)}
                            for i, s in enumerate(stages)], gas=g,
                  data_from=rows[0][0][:10], data_to=rows[-1][0][:10], readings=len(rows))

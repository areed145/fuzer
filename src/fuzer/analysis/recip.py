"""Reciprocating compressor checks (API 618, ISO 20816-8): what a recip specialist looks at first, as calcs
({value, expected, flag, basis, sources}).

- `rod_load(ref)`: the gas rod load on each throw from its stage's suction and discharge pressure and its cylinder's
  bore and rod (the maker's frame and cylinder data), against the frame's rating. In compression the head end is at
  discharge pressure and the crank end at suction; in tension the reverse:
      compression = Pd x A_head - Ps x (A_head - A_rod)        tension = Pd x (A_head - A_rod) - Ps x A_head
  (gauge pressures: the rod's outer end sits in air). Over the rating breaks crossheads, pins and rods (`rod_load_high`).
  A load that doesn't change sign each revolution starves the crosshead pin of oil (`no_reversal`); this checks the
  gas loads only, since inertia needs the crank-angle data a condition-monitoring system has.
- `valve_health(ref)`: the most common recip failure, read from process data. A leaking suction or discharge valve
  recompresses gas it already moved, so its cylinder runs hotter than the others on the same stage, and the stage
  loses capacity, which shifts the pressure ratio between stages (a leak on the 2nd stage pushes the interstage
  pressure up and the 1st stage works harder). Flags `valve_leak` (a cylinder 18 °F or more above its stage and the
  gap grown since the window's first week) and `ratio_shift` (the 1st stage's share of the total ratio moved by 0.04
  or more): the stage whose ratio fell holds the leaking valve.

Frame vibration is `rotating.vibration`, judged against the maker's alarm and trip rather than the centrifugal zones.
"""

import math
import re
from datetime import timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of

HOT = 18.0  # °F a cylinder runs above its stage's others: a leaking valve
GROWN = 11.0  # °F the gap grew since the window's first week
SHIFT = 0.04  # change in the 1st stage's share of the total (log) pressure ratio
ATM = 14.7  # psi: gauge to absolute
REVERSAL = 0.05  # a direction's load under this share of the other: the load hardly reverses


def geometry(conn, tag: str) -> dict:
    """Each throw's stage, bore and rod (in) and the frame's gas rod load rating (lbf), from the maker's frame and
    cylinder data for the machine's model."""
    from ..sources import curves

    c = curves.for_machine(conn, tag)  # the machine's own data, else its model's (instance_of)
    if not (c or {}).get("series", {}).get("bore@throw"):
        m = conn.execute("select m.dst from edge i join edge m on m.src = i.src and m.rel = 'instance_of' "
                         "where i.dst = %s and i.rel = 'installed_at' and i.valid_to is null limit 1", (tag,)).fetchone()
        c = curves.for_model(conn, m["dst"]) if m else c
    s = (c or {}).get("series", {})
    if not c or not s.get("bore@throw") or not s.get("stage@throw"):
        raise CalcError("no cylinder data (bore, rod and stage per throw) for this machine's model: the maker's "
                        "frame and cylinder sheet gives them")
    rod = {int(x): y for x, y in s.get("rod@throw", [])}
    stage = {int(x): int(y) for x, y in s["stage@throw"]}
    limit = c["ratings"].get("rod_load_limit")
    return {"throws": {int(x): {"bore_in": y, "rod_in": rod.get(int(x)), "stage": stage.get(int(x))}
                       for x, y in s["bore@throw"]},
            "limit_lbf": limit[1] if limit else None, "model": c["model_id"], "source": c["source"]}


def _points(conn, tag: str) -> dict:
    """Suction, interstage and discharge pressure and each cylinder's discharge temperature, by what they measure."""
    from ..sources.timeseries import _unit_points

    out: dict = {"cylinders": {}}
    for p in _unit_points(conn, tag, 40):
        d = (p["description"] or "").lower()
        if m := re.search(r"cylinder (\d+) discharge temperature", d):
            out["cylinders"][int(m.group(1))] = p
        elif "interstage pressure" in d:
            out["pi"] = p
        elif "suction pressure" in d:
            out["ps"] = p
        elif "discharge pressure" in d:
            out["pd"] = p
        elif (p["uom"] or "") == "rpm":
            out["speed"] = p
    return out


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def _running(v: dict, pts: dict) -> list[str]:
    """Hours the machine ran: its speed above half its median, else its stage pressures apart."""
    if "speed" in v:
        sp = v["speed"]
        med = float(np.median(list(sp.values()))) if sp else 0
        return sorted(t for t, x in sp.items() if x > 0.5 * med)
    return sorted(t for t in v["pd"] if v["pd"][t] - v["ps"].get(t, v["pd"][t]) > 5)


def rod_load(conn, fid: str, days: int = 30, until: str | None = None, recent_days: int = 7, src=None) -> dict:
    tag = _tag(conn, fid)
    g = geometry(conn, tag)
    pts = _points(conn, tag)
    stages = sorted({t["stage"] for t in g["throws"].values()})
    need = ["ps", "pd"] + (["pi"] if len(stages) > 1 else [])
    if missing := [k for k in need if k not in pts]:
        raise CalcError("no " + ", ".join({"ps": "suction", "pi": "interstage", "pd": "discharge"}[k] for k in missing)
                        + " pressure point on that compressor")
    start, end = window_of(conn, days, until)
    keys = {k: pts[k] for k in ("ps", "pi", "pd", "speed") if k in pts}
    v = read(keys, start, end, 60, src)
    run = _running(v, pts)
    if len(run) < 24:
        raise CalcError("the compressor hardly ran in that window")
    edges = {1: ("ps", "pi" if len(stages) > 1 else "pd"), 2: ("pi", "pd")}
    cut = (end - timedelta(days=recent_days)).isoformat()
    throws, worst = [], None
    for n, t in sorted(g["throws"].items()):
        a_h = math.pi / 4 * t["bore_in"] ** 2  # in2
        a_r = math.pi / 4 * (t["rod_in"] or 0) ** 2
        lo, hi = edges[t["stage"]]
        comp, tens, times = [], [], []
        for ts in run:
            ps, pd = v[lo].get(ts), v[hi].get(ts)
            if ps is None or pd is None:
                continue
            comp.append(pd * a_h - ps * (a_h - a_r))  # psig x in2 = lbf
            tens.append(pd * (a_h - a_r) - ps * a_h)
            times.append(ts)
        recent = [i for i, ts in enumerate(times) if ts >= cut] or list(range(len(times)))
        c95 = float(np.percentile([comp[i] for i in recent], 95))
        t95 = float(np.percentile([tens[i] for i in recent], 95))
        reversal = float(np.percentile([min(comp[i], tens[i]) / max(comp[i], tens[i], 1e-9) for i in recent], 5))
        over = [times[i] for i in range(len(times)) if g["limit_lbf"] and max(comp[i], tens[i]) > g["limit_lbf"]]
        daily: dict[str, list[float]] = {}
        for i, ts in enumerate(times):
            daily.setdefault(ts[:10], []).append(max(comp[i], tens[i]))
        row = {"throw": n, "stage": t["stage"], "compression_lbf": round(c95), "tension_lbf": round(t95),
               "pct_of_rating": round(max(c95, t95) / g["limit_lbf"] * 100, 1) if g["limit_lbf"] else None,
               "reversal_ratio": round(reversal, 3), "hours_over_rating": len(over),
               "first_over_rating": over[0][:16] if over else None,
               "daily": [{"day": d, "lbf": round(float(np.median(x)))} for d, x in sorted(daily.items())][-60:]}
        throws.append(row)
        if worst is None or max(c95, t95) > max(worst["compression_lbf"], worst["tension_lbf"]):
            worst = row
    peak = max(worst["compression_lbf"], worst["tension_lbf"])
    rev = min(throws, key=lambda r: r["reversal_ratio"])
    flag = ("rod_load_high" if g["limit_lbf"] and peak > g["limit_lbf"] else
            "no_reversal" if rev["reversal_ratio"] < REVERSAL else None)
    return result(peak, g["limit_lbf"], flag,
                  "gas rod load per throw from its stage's suction and discharge pressure (gauge) and its bore and rod: "
                  "compression Pd x A_head - Ps x (A_head - A_rod), tension Pd x (A_head - A_rod) - Ps x A_head (API "
                  f"618); the last {recent_days} days' 95th percentile while running, against the frame's "
                  + (f"{g['limit_lbf']:,.0f} lbf gas rod load rating" if g["limit_lbf"] else "rating (none on record)")
                  + f" ({g['source']} data for {_label(conn, g['model'])}). Gas loads only: inertia needs crank-angle "
                  "data", [tag, g["model"], *[p["id"] for p in keys.values()]], unit="lbf",
                  worst_throw=worst["throw"], pct_of_rating=worst["pct_of_rating"], throws=throws,
                  least_reversal_throw=rev["throw"], reversal_ratio=rev["reversal_ratio"],
                  hours_over_rating=sum(r["hours_over_rating"] for r in throws),
                  first_over_rating=min((r["first_over_rating"] for r in throws if r["first_over_rating"]), default=None),
                  data_from=run[0][:10], data_to=run[-1][:10], readings=len(run))


def valve_health(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    tag = _tag(conn, fid)
    pts = _points(conn, tag)
    if len(pts["cylinders"]) < 2:
        raise CalcError("fewer than two cylinder discharge temperatures on that compressor: a valve leak shows as one "
                        "cylinder hotter than its neighbours")
    try:
        stage_of = {n: t["stage"] for n, t in geometry(conn, tag)["throws"].items()}
    except CalcError:
        stage_of = {n: 1 for n in pts["cylinders"]}  # without the cylinder sheet, compare them all
    start, end = window_of(conn, days, until)
    keys = {f"c{n}": p for n, p in pts["cylinders"].items()} | {k: pts[k] for k in ("ps", "pi", "pd", "speed") if k in pts}
    v = read(keys, start, end, 60, src)
    run = _running(v, pts) if ("speed" in v or ("pd" in v and "ps" in v)) else sorted(v["c" + str(min(pts["cylinders"]))])
    if len(run) < 48:
        raise CalcError("the compressor hardly ran in that window")
    first_cut = (start + timedelta(days=7)).isoformat()
    last_cut = (end - timedelta(days=3)).isoformat()
    first = [t for t in run if t < first_cut] or run[:72]
    last = [t for t in run if t >= last_cut] or run[-72:]
    cyl, worst = [], None
    for n in sorted(pts["cylinders"]):
        mates = [m for m in pts["cylinders"] if m != n and stage_of.get(m) == stage_of.get(n)]
        if not mates:
            continue

        def gap(ts: list[str]) -> float:
            d = [v[f"c{n}"][t] - float(np.median([v[f"c{m}"][t] for m in mates])) for t in ts  # noqa: B023
                 if t in v[f"c{n}"] and all(t in v[f"c{m}"] for m in mates)]  # noqa: B023
            return float(np.median(d)) if d else 0.0
        now, then = gap(last), gap(first)
        row = {"cylinder": n, "stage": stage_of.get(n), "above_stage_f": round(now, 1), "at_start_f": round(then, 1),
               "point": pts["cylinders"][n]["point"],
               "temp_f": round(float(np.median([v[f"c{n}"][t] for t in last if t in v[f"c{n}"]])), 1)}
        cyl.append(row)
        if worst is None or now > worst["above_stage_f"]:
            worst = row
    if worst is None:
        raise CalcError("no two cylinders on the same stage to compare")
    ratio = None
    if all(k in v for k in ("ps", "pi", "pd")):
        def share(ts: list[str]) -> float:
            s = [math.log((v["pi"][t] + ATM) / (v["ps"][t] + ATM)) / math.log((v["pd"][t] + ATM) / (v["ps"][t] + ATM))
                 for t in ts if all(t in v[k] for k in ("ps", "pi", "pd")) and v["pd"][t] > v["ps"][t] + 15]
            return float(np.median(s)) if s else float("nan")
        a, b = share(first), share(last)
        ratio = {"first_stage_share_then": round(a, 3), "first_stage_share_now": round(b, 3), "change": round(b - a, 3),
                 "interstage_psig": round(float(np.median([v["pi"][t] for t in last if t in v["pi"]])), 1)}
    leak = worst["above_stage_f"] >= HOT and worst["above_stage_f"] - worst["at_start_f"] >= GROWN
    shifted = ratio is not None and abs(ratio["change"]) >= SHIFT
    flag = "valve_leak" if leak else "ratio_shift" if shifted else None
    weak = None
    if ratio and shifted:
        weak = 2 if ratio["change"] > 0 else 1  # the stage whose share fell lost capacity
    return result(worst["above_stage_f"], 0.0, flag,
                  "each cylinder's discharge temperature against the median of the others on its stage, hourly while "
                  f"running: the last 3 days against the window's first week; leak at {HOT:g} °F above its stage and "
                  f"{GROWN:g} °F more than at the start. The stage pressure ratios split as the 1st stage's share of "
                  f"the total (log) ratio; a shift of {SHIFT:g} or more means a stage lost capacity",
                  [tag, *[p["id"] for p in keys.values()]], unit="°F above its stage", hottest_cylinder=worst["cylinder"],
                  hottest_stage=worst["stage"], cylinders=cyl, stage_ratio=ratio, weak_stage=weak,
                  data_from=run[0][:10], data_to=run[-1][:10], readings=len(run))

"""Back-pressure calcs (#107 §2): what holds wells back that never raises a deferment record.

Simple and explainable, no network solver. A well delivers in proportion to its shut-in wellhead pressure
less its flowing one (a linear inflow at the wellhead). The flowing pressure is the back-pressure plus the
drop across the choke and flowline, which grows with rate squared. While the choke is subcritical (back /
wellhead pressure 0.55 or more) the back-pressure reaches the well; at critical flow it doesn't.

- `pressure_profile(well)`: the pressure at each node from the wellhead down its flow path (jacket
  header, separator, compressor suction) and the drop across each segment.
- `well_constraint(well)`: shut in, choke-limited (critical flow) or back-pressure-limited (subcritical),
  with lift-limited added when the well's gas lift curve says more gas pays.
- `backpressure_sensitivity(scope)`: oil change per bar of back-pressure for each subcritical well, two
  ways that are reported side by side: from the inflow and choke equations, and by regression of its well
  tests on the header pressure at the time (with the decline taken out). Flagged where they disagree.
- `dp_drift(well)`: the wellhead-to-header pressure drop drifting up (a flowline waxing or scaling) or
  stepping, with when it started (signals.detect_changes).
- `headroom(unit)`: each point on a unit against its nearest limit (#101): what a debottleneck runs into next.

Each returns the calcs convention: {value, expected, deviation, flag, basis, sources, ...}.
"""

import itertools
from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, result, window_of

RISE_BAR, RISE_SHARE = 0.7, 0.4  # a rising pressure drop must be up this much (bar) and this share
CRITICAL = 0.55  # back / wellhead pressure (absolute) under this: the choke is at critical flow
ATM = 1.013


def _pt(conn, tag_label: str, ending: str = "Pressure") -> dict | None:
    from ..sources.timeseries import _unit_points

    t = conn.execute("select fuzer_id from entity_label where label = %s", (tag_label,)).fetchone()
    if not t:
        return None
    return next((p for p in _unit_points(conn, t["fuzer_id"], 20) if (p["description"] or "").endswith(ending)), None)


def well_points(conn, well: str) -> dict:
    """A well's wellhead pressure and choke position points, and its back-pressure point (the jacket's
    production header, as plot_choke reads it)."""
    from ..sources.timeseries import _unit_points

    pts = _unit_points(conn, well, 20, children=False)
    by = {k: next((p for p in pts if (p["description"] or "").endswith(end)), None)
          for k, end in (("whp", "Wellhead Pressure"), ("choke", "Choke Position"), ("wht", "Wellhead Temperature"))}
    choke = conn.execute("select e.src from edge e join entity_label l on l.fuzer_id = e.src "
                         "where e.rel = 'part_of' and e.dst = %s and l.label like '%%-HCV-%%' limit 1", (well,)).fetchone()
    area = _label(conn, choke["src"]).split("-")[0] if choke else None
    by["back"] = _pt(conn, f"{area}-PT-3002") if area else None
    if not by["whp"]:
        raise CalcError("the well has no wellhead pressure point")
    return by


def _now(conn, pts: dict, at: str | None, hours: int = 24, src=None) -> tuple[dict, datetime]:
    """Each point's median over the `hours` before `at`."""
    from . import calcs

    end = datetime.fromisoformat(at) if at else window_of(conn, 1)[1]
    v = calcs.read({k: p for k, p in pts.items() if p}, end - timedelta(hours=hours), end, 30, src)
    return {k: float(np.median(list(x.values()))) if x else None for k, x in v.items()}, end


def pressure_profile(conn, well: str, at: str | None = None, src=None) -> dict:
    """Pressures from the wellhead down the well's flow path and the drop across each segment."""
    from ..graph import edges

    pts = well_points(conn, well)
    path = [("wellhead", pts["whp"]), ("jacket production header", pts["back"])]
    seen = {p["id"] for _, p in path if p}
    for r in edges.trace(conn, well, "flows_to", "down", None, 4):
        p = _pt(conn, r["label"]) if r.get("label") else None
        if p and p["id"] not in seen and (p["uom"] or "").startswith("bar"):
            seen.add(p["id"])
            path.append((r["label"], p))
    vals, end = _now(conn, {i: p for i, (_, p) in enumerate(path) if p}, at, src=src)
    nodes = [{"node": n, "point": p["point"] if p else None, "pressure_barg": round(vals[i], 2) if p and vals.get(i) is not None
              else None} for i, (n, p) in enumerate(path)]
    known = [x for x in nodes if x["pressure_barg"] is not None]
    segs = [{"from": a["node"], "to": b["node"], "dp_bar": round(a["pressure_barg"] - b["pressure_barg"], 2)}
            for a, b in itertools.pairwise(known)]
    biggest = max(segs, key=lambda s: s["dp_bar"]) if segs else None
    return result(biggest["dp_bar"] if biggest else None, None, None,
                  f"median of each pressure over the 24 h to {end:%Y-%m-%d %H:%M}, down the flows_to path",
                  [well, *[p["id"] for _, p in path if p]], unit="bar", nodes=nodes, segments=segs,
                  largest_drop=biggest)


def well_constraint(conn, well: str, at: str | None = None, src=None) -> dict:
    """What limits the well: `shut_in`; `choke` (critical flow: back-pressure doesn't reach it, opening the
    choke would); `back_pressure` (subcritical: the header holds it back). `lift`: also, its gas lift curve
    says more gas pays."""
    from . import calcs

    pts = well_points(conn, well)
    if not pts["back"]:
        raise CalcError("no production header pressure for the well's jacket")
    v, end = _now(conn, {k: pts[k] for k in ("whp", "back", "choke")}, at, hours=6, src=src)
    whp, back, opening = v["whp"], v["back"], v.get("choke")
    ratio = (back + ATM) / (whp + ATM)
    flowing = (opening is None or opening > 2) and whp - back > 0.2
    flag = "shut_in" if not flowing else "choke" if ratio < CRITICAL else "back_pressure"
    lift = None
    if flowing:
        try:
            lift = calcs.gas_lift(conn, well, until=end.isoformat())["flag"] == "more_gas"
        except CalcError:
            lift = None
    return result(round(ratio, 3), CRITICAL, flag,
                  f"back over wellhead pressure (absolute), medians over 6 h to {end:%Y-%m-%d %H:%M}; under {CRITICAL} the "
                  "choke is at critical flow", [well, pts["whp"]["id"], pts["back"]["id"]], unit="pressure ratio",
                  whp_barg=round(whp, 2), back_barg=round(back, 2), choke_pct=round(opening, 1) if opening is not None else None,
                  lift_limited=lift)


def _shut_in_whp(conn, pts: dict, end: datetime, days: int = 365, src=None) -> float | None:
    """The well's wellhead pressure while shut in (choke closed) over the last year: its inflow's end point."""
    from . import calcs

    if not pts.get("choke"):
        return None
    v = calcs.read({"whp": pts["whp"], "choke": pts["choke"]}, end - timedelta(days=days), end, 360, src)
    shut = [v["whp"][t] for t, c in v["choke"].items() if c < 2 and t in v["whp"]]
    return float(np.median(shut)) if len(shut) >= 2 else None


def _wells(conn, scope: str) -> list[str]:
    kind = conn.execute("select kind from entity where fuzer_id = %s", (scope,)).fetchone() if scope != "facility" else None
    if kind and kind["kind"] == "well":
        return [scope]
    if scope == "facility":
        rows = conn.execute("select fuzer_id from entity where kind = 'well' order by 1")
    else:
        rows = conn.execute("select e.src as fuzer_id from edge e join entity w on w.fuzer_id = e.src and w.kind = 'well' "
                            "where e.rel in ('part_of', 'located_at') and e.dst = %s order by 1", (scope,))
    return [r["fuzer_id"] for r in rows]


def backpressure_sensitivity(conn, scope: str, at: str | None = None, src=None, limit: int = 200) -> dict:
    """Oil change per bar of back-pressure (Sm3/d per bar, negative: more back-pressure, less oil) for
    each flowing subcritical well in scope, model-based and data-based, summed per header."""
    out, by_header = sensitivities(conn, scope, at, src, limit)
    total = round(sum(r["model_sm3d_per_bar"] or 0 for r in out), 2)
    disagree = [r["well"] for r in out if r["agree"] is False]
    return result(total, None, "model_and_data_disagree" if disagree else None,
                  "model: linear inflow at the wellhead to the shut-in pressure, choke and flowline drop growing with "
                  "rate squared, differentiated at today's point; data: the well's tests against the header pressure "
                  "at the time, decline taken out", [r["latest_test"] for r in out][:20], unit="Sm3/d oil per bar",
                  wells=sorted(out, key=lambda r: r["model_sm3d_per_bar"] or 0)[:50], by_header=list(by_header.values()),
                  disagree=disagree)


def sensitivities(conn, scope: str, at: str | None = None, src=None, limit: int = 200) -> tuple[list[dict], dict]:
    """Every back-pressure-limited well's sensitivity row, and the sums per header."""
    from . import calcs

    out, by_header = [], {}
    end = datetime.fromisoformat(at) if at else window_of(conn, 1)[1]
    for well in _wells(conn, scope)[:limit]:
        try:
            pts = well_points(conn, well)
            c = well_constraint(conn, well, end.isoformat(), src)
            tests = calcs.well_tests(conn, well, end.isoformat())
        except CalcError:
            continue
        if c["flag"] != "back_pressure" or not pts["back"]:
            continue
        t = tests[-1]
        wc = min(t.get("water_cut", 0) or 0, 98) / 100
        q = (t.get("oil_rate") or 0) / (1 - wc)
        psi = _shut_in_whp(conn, pts, end, src=src)
        model = None
        if psi and q > 0 and psi > c["whp_barg"]:
            k = (c["whp_barg"] - c["back_barg"]) / q ** 2  # choke and flowline, bar per (Sm3/d)^2
            J = q / (psi - c["whp_barg"])
            model = round(-J / (1 + 2 * J * k * q) * (1 - wc), 3)
        data = _test_regression(conn, pts, tests, end, src)
        agree = None if model is None or data is None else (model * data > 0 and 0.5 <= model / data <= 2)
        row = {"well": _label(conn, well), "id": well, "header": pts["back"]["point"], "oil_sm3d": round(t.get("oil_rate") or 0, 1),
               "model_sm3d_per_bar": model, "data_sm3d_per_bar": data, "agree": agree, "shut_in_whp": psi,
               "latest_test": t["record"]}
        out.append(row)
        row |= {"whp_barg": c["whp_barg"], "back_barg": c["back_barg"], "choke_pct": c["choke_pct"]}
        h = by_header.setdefault(pts["back"]["point"], {"header": pts["back"]["point"], "wells": 0, "model_sm3d_per_bar": 0.0,
                                                         "back_barg": c["back_barg"]})
        h["wells"] += 1
        h["model_sm3d_per_bar"] = round(h["model_sm3d_per_bar"] + (model or 0), 3)
    return out, by_header


def _test_regression(conn, pts: dict, tests: list[dict], end: datetime, src=None, days: int = 365) -> float | None:
    """Slope of oil rate on the header pressure at each test, with a time trend (decline) taken out."""
    from . import calcs

    recent = [t for t in tests if t.get("oil_rate") and t["at"] >= end - timedelta(days=days)]
    if len(recent) < 5:
        return None
    v = calcs.read({"back": pts["back"]}, recent[0]["at"].replace(hour=0, minute=0), end, 60, src)["back"]
    rows = []
    for t in recent:
        key = t["at"].replace(hour=12, minute=0).isoformat(timespec="minutes")
        if key in v:
            rows.append(((t["at"] - recent[0]["at"]).days / 365.25, v[key], t["oil_rate"]))
    if len(rows) < 5 or np.ptp([r[1] for r in rows]) < 0.5:
        return None  # the header hardly moved: nothing to learn from
    X = np.array([[1.0, r[0], r[1]] for r in rows])
    coef = np.linalg.lstsq(X, np.array([r[2] for r in rows]), rcond=None)[0]
    return round(float(coef[2]), 3)


def dp_drift(conn, well: str, days: int = 60, until: str | None = None, src=None) -> dict:
    """The wellhead-to-header pressure drop over `days`: rising (a flowline restriction building, wax or
    scale: a drift still going at the end and up `RISE_BAR` and `RISE_SHARE` on where it started, more than
    the rate's own wander between tests moves it) or stepping (a choke move, a reroute), with when it
    started."""
    from . import calcs, signals

    pts = well_points(conn, well)
    if not pts["back"]:
        raise CalcError("no production header pressure for the well's jacket")
    start, end = window_of(conn, days, until)
    v = calcs.read({k: pts[k] for k in ("whp", "back", "choke", "wht") if pts.get(k)}, start, end, 60, src)
    times = sorted(set(v["whp"]) & set(v["back"]))
    dp = np.array([v["whp"][t] - v["back"][t] for t in times])
    found = signals.detect_changes([datetime.fromisoformat(t) for t in times], dp, 60, window_minutes=720,
                                   th=signals.thresholds(conn))
    rising = [c for c in found if c["kind"] == "drift" and c["after"] > c["before"] and
              c["end"] >= (end - timedelta(days=2)).isoformat(timespec="minutes")]
    first = float(np.median(dp[: max(24, len(dp) // 10)])) if len(dp) else None
    now = float(np.median(dp[-24:])) if len(dp) else None
    seg = rising[-1] if rising else None  # measured on the drift itself, so a step before it doesn't count
    big = seg is not None and seg["after"] - seg["before"] >= max(RISE_BAR, RISE_SHARE * abs(seg["before"]))
    if big and v.get("wht"):  # more flow (a warmer wellhead) raises the drop too: a restriction cuts the flow
        w = [x for t, x in sorted(v["wht"].items()) if t >= seg["start"]]
        if len(w) >= 48 and np.median(w[-24:]) - np.median(w[:24]) > 0.5:
            big = False
    if rising and v.get("choke"):  # the choke moving changes the drop too: that isn't the flowline
        since = rising[-1]["start"]
        moved = [x for t, x in v["choke"].items() if t >= since]
        if moved and max(moved) - min(moved) > 2:
            big = False
    return result(round(now, 2) if now is not None else None,
                  round(seg["before"] if big else first, 2) if first is not None else None,
                  "dp_rising" if rising and big else None,
                  f"wellhead minus jacket header pressure, hourly over {days} days; expected is where a rise began, "
                  "else its first days",
                  [well, pts["whp"]["id"], pts["back"]["id"]], unit="bar",
                  rising_since=rising[-1]["start"] if rising else None,
                  changes=[{k: c.get(k) for k in ("kind", "start", "end", "before", "after", "per_day")} for c in found][-8:])


def headroom(conn, pack, unit: str, at: str | None = None, src=None) -> dict:
    """Each point on a unit against its nearest limit (#101: alarm, IOW or learned range): the share of
    the limits' span left, smallest first."""
    from ..sources.timeseries import _unit_points
    from . import limits

    pts = list(_unit_points(conn, unit, 30))
    if not pts:
        raise CalcError("no PI points on that unit")
    vals, end = _now(conn, {p["id"]: p for p in pts}, at, hours=24, src=src)
    lims = limits.for_points(conn, pack, [p["id"] for p in pts], end)
    rows = []
    for p in pts:
        v = vals.get(p["id"])
        ls = limits.lines(lims[p["id"]])
        lo = max((x["value"] for x in ls if x["type"] in ("lo", "lolo")), default=None)
        hi = min((x["value"] for x in ls if x["type"] in ("hi", "hihi")), default=None)
        if v is None or (lo is None and hi is None):
            continue
        span = (hi - lo) if lo is not None and hi is not None and hi > lo else abs(hi if hi is not None else lo) or 1.0
        room = min(((hi - v) / span) if hi is not None else 1.0, ((v - lo) / span) if lo is not None else 1.0)
        rows.append({"point": p["point"], "description": p["description"], "value": round(v, 2), "lo": lo, "hi": hi,
                     "headroom_pct": round(room * 100, 1), "nearest": "hi" if hi is not None and (lo is None or hi - v < v - lo) else "lo"})
    rows.sort(key=lambda r: r["headroom_pct"])
    if not rows:
        raise CalcError("no limits on that unit's points")
    return result(rows[0]["headroom_pct"], None, "at_limit" if rows[0]["headroom_pct"] <= 10 else None,
                  f"median over the 24 h to {end:%Y-%m-%d %H:%M} against the nearest alarm, IOW or learned limit, as a "
                  "share of the span between its limits", [unit, *[p["id"] for p in pts][:10]], unit="% of span",
                  points=rows[:15], tightest=rows[0])


# ---- debottleneck options (#107 §3) ---------------------------------------------------------------

def _separator_of(conn, well: str, cache: dict) -> str | None:
    """The first separator (a -V- tag) down a well's flow path."""
    from ..graph import edges

    if well not in cache:
        cache[well] = next((r["fuzer_id"] for r in edges.trace(conn, well, "flows_to", "down", None, 4)
                            if r["kind"] == "tag" and "-V-" in (r["label"] or "")), None)
    return cache[well]


def debottleneck_options(conn, pack, scope: str = "facility", at: str | None = None, src=None, limit: int = 10) -> dict:
    """Candidate actions against back-pressure, ranked by oil gained (Sm3/d), each with its basis,
    confidence, the next constraint it runs into and what would confirm it. Proposals for people, not
    operating instructions."""
    from . import limits

    rows, _ = sensitivities(conn, scope, at, src, limit=1000)
    rows = [r for r in rows if r["model_sm3d_per_bar"] is not None]
    end = datetime.fromisoformat(at) if at else window_of(conn, 1)[1]
    options, cache = [], {}

    by_sep: dict[str, list[dict]] = {}
    for r in rows:
        if sep := _separator_of(conn, r["id"], cache):
            by_sep.setdefault(sep, []).append(r)
    for sep, ws in by_sep.items():
        p = _pt(conn, _label(conn, sep))
        if not p:
            continue
        v, _ = _now(conn, {"p": p}, end.isoformat(), src=src)
        lines = limits.lines(limits.for_points(conn, pack, [p["id"]], end)[p["id"]])
        floor = [(x["value"], f"{x['basis']} {x['type']} {x['value']:g} barg" + (f" ({x['iow']})" if x.get("iow") else ""))
                 for x in lines if x["type"] in ("lo", "lolo") and x["value"] < v["p"]]
        if not floor:
            continue
        bound, what = max(floor, key=lambda f: f[0] if "iow" in f[1] else f[0] - 100)  # an IOW binds before an alarm
        room = round(v["p"] - bound, 2)
        per_bar = -sum(r["model_sm3d_per_bar"] for r in ws)
        if room > 0.1:
            options.append({"action": f"Lower {_label(conn, sep)} pressure by {room:g} bar, to its {what}",
                            "kind": "lower_pressure", "target": sep, "gain_sm3d": round(per_bar * room, 1),
                            "per_bar_sm3d": round(per_bar, 2), "wells": len(ws), "confidence": "medium",
                            "next_constraint": f"{_label(conn, sep)} {what}; compressor suction follows the separator down",
                            "side_effect": "lower separator pressure: more gas to recompress, check compressor suction and power headroom",
                            "confirm_by": "a stepped setpoint change with well tests before and after",
                            "basis": f"model sensitivity of {len(ws)} back-pressure-limited wells, summed, times the room to the limit"})

    headers = sorted({(r["header"], r["back_barg"]) for r in rows}, key=lambda h: h[1])
    for r in rows:
        low = next((h for h in headers if h[1] < r["back_barg"] - 1.0), None)
        if low:
            dp = r["back_barg"] - low[1]
            options.append({"action": f"Reroute {r['well']} from header {r['header']} to {low[0]} ({dp:.1f} bar lower)",
                            "kind": "reroute", "target": r["id"], "gain_sm3d": round(-r["model_sm3d_per_bar"] * dp, 1),
                            "confidence": "low", "next_constraint": f"capacity of the line to {low[0]}; its other wells see more back-pressure",
                            "side_effect": "needs a route between the headers (check the P&IDs)",
                            "confirm_by": "a well test on the new route", "basis": "model sensitivity times the header difference"})
        if r["choke_pct"] is not None and r["choke_pct"] < 80:
            dp = 0.5 * (r["whp_barg"] - r["back_barg"])
            options.append({"action": f"Open {r['well']}'s choke from {r['choke_pct']:.0f}%", "kind": "open_choke", "target": r["id"],
                            "gain_sm3d": round(-r["model_sm3d_per_bar"] * dp, 1), "confidence": "low",
                            "next_constraint": "sand or water coning at a higher drawdown; the header's other wells",
                            "side_effect": "higher drawdown", "confirm_by": "a step-rate test",
                            "basis": "model sensitivity times half the drop across the choke"})
    for r in rows:
        try:
            d = dp_drift(conn, r["id"], 45, end.isoformat(timespec="minutes"), src)
        except CalcError:
            continue
        if d["flag"] == "dp_rising":
            dp = d["value"] - d["expected"]
            options.append({"action": f"Pig or clean {r['well']}'s flowline (pressure drop up {dp:.1f} bar since {d['rising_since'][:10]})",
                            "kind": "pig_flowline", "target": r["id"], "gain_sm3d": round(-r["model_sm3d_per_bar"] * dp, 1),
                            "confidence": "medium", "next_constraint": "the well's choke and the header",
                            "side_effect": "pigging run", "confirm_by": "the pressure drop back at its baseline after pigging",
                            "basis": "model sensitivity times the rise in the wellhead-to-header drop"})
    options.sort(key=lambda o: -o["gain_sm3d"])
    for i, o in enumerate(options, 1):
        o["rank"] = i
    top = options[0] if options else None
    return result(top["gain_sm3d"] if top else 0.0, None, None,
                  "ranked by oil gained from the back-pressure model (IPR and choke) at today's operating point; "
                  "proposals for people, not operating instructions", [r["latest_test"] for r in rows][:20],
                  unit="Sm3/d oil", at=end.isoformat(timespec="minutes"), options=options[:limit], considered=len(options))


def store_options(conn, pack, at: str | None = None, src=None, keep: int = 25) -> dict:
    """Replace the stored debottleneck options with a fresh scan (the work board and value score read them)."""
    from psycopg.types.json import Jsonb

    out = debottleneck_options(conn, pack, "facility", at, src, limit=keep)
    conn.execute("delete from debottleneck_option")
    for o in out["options"]:
        conn.execute("insert into debottleneck_option (as_of, rank, kind, target, action, gain_sm3d, detail) "
                     "values (%s, %s, %s, %s, %s, %s, %s)", (out["at"], o["rank"], o["kind"], o["target"], o["action"],
                                                             o["gain_sm3d"], Jsonb(o)))
    conn.commit()
    return {"options": len(out["options"]), "considered": out["considered"], "top": out["options"][:1]}

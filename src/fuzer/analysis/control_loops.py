"""Control loops (#355): a loop from the DCS loop list as one thing: its controller tag, the item it holds, and the PI
points of its process value, setpoint, controller output and mode, read together on one time grid. The loop health
ranking and the stiction check build on this. Read-only: Fuzer never writes to the historian or the DCS.

    loop(conn, ref)                         the loop and its four points by role
    series(conn, ref, days, until, step)    its PV, SP, OP and mode over the window, aligned
    health(conn, days, until)               every loop ranked for the controls engineer's worklist (#356): time in
                                            manual, output saturated, setpoint error and a regular oscillation

The ranking is a worklist for review, not a change request: it never proposes a tuning, setpoint or mode change.
"""

from .calcs import CalcError, read, window_of

ROLES = ("pv", "sp", "op", "mode")
MANUAL_PCT = 5.0  # % of the window in manual: the loop isn't doing its job
SATURATED_PCT = 10.0  # % of the time in auto with the output at a limit (within 1 % of 0 or 100): out of valve
ERROR_PCT = 2.0  # mean absolute setpoint error in auto, as % of the setpoint: not holding its setpoint
OSCILLATION_SCORE = 50.0  # what a regular oscillation adds to a loop's score


def loops(conn) -> list[dict]:
    """Every control loop on the loop list, with its points by role."""
    rows = conn.execute(
        """
        select e.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value #>> '{}') as f
        from entity e join entity_label l using (fuzer_id) join golden g using (fuzer_id)
        where e.kind = 'control_loop' and e.retired_at is null and e.merged_into is null
        group by e.fuzer_id, l.label order by l.label
        """).fetchall()
    return [_shape(conn, r) for r in rows]


def _shape(conn, r) -> dict:
    from ..sources import timeseries

    pts = {}
    for p in conn.execute(
            "select e.dst, l.label from edge e join entity_label l on l.fuzer_id = e.dst "
            "where e.src = %s and e.rel = 'has_timeseries' and e.valid_to is null", (r["id"],)):
        role = next((k for k in ROLES if (r["f"].get(f"{k}_point") or "").upper() == p["label"].upper()), None)
        if role:
            pts[role] = timeseries._point(conn, p["dst"])
    item = conn.execute(
        "select l.label from edge e join entity_label l on l.fuzer_id = e.dst "
        "where e.src = %s and e.rel = 'located_at' and e.valid_to is null", (r["id"],)).fetchone()
    return {"id": r["id"], "loop": r["label"], "description": r["f"].get("description"),
            "holds": item["label"] if item else None, "final_element": r["f"].get("final_element"),
            "unit": r["f"].get("eng_units"), "points": {k: (pts[k]["point"] if pts.get(k) else None) for k in ROLES},
            "missing": [k for k in ROLES if not pts.get(k)], "_series": pts}


def loop(conn, ref: str) -> dict:
    """One loop by its fuzer id or controller tag."""
    r = conn.execute(
        """
        select e.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value #>> '{}') as f
        from entity e join entity_label l using (fuzer_id) join golden g using (fuzer_id)
        where e.kind = 'control_loop' and (e.fuzer_id = %s or upper(l.label) = upper(%s))
        group by e.fuzer_id, l.label
        """, (ref, ref)).fetchone()
    if r is None:
        raise CalcError("no control loop by that name on the loop list")
    return _shape(conn, r)


def series(conn, ref: str, days: int = 3, until: str | None = None, step: int = 5, src=None) -> dict:
    """A loop's PV, SP, OP and mode on one grid over the `days` up to `until`."""
    lp = loop(conn, ref)
    if lp["missing"]:
        raise CalcError(f"{lp['loop']} has no {', '.join(lp['missing'])} point on the historian")
    start, end = window_of(conn, days, until)
    vals = read({k: p for k, p in lp["_series"].items()}, start, end, step, src)
    times = sorted(set.intersection(*(set(v) for v in vals.values())))
    return {k: v for k, v in lp.items() if k != "_series"} | {
        "since": start.isoformat(timespec="minutes"), "until": end.isoformat(timespec="minutes"), "step_min": step,
        "times": times, **{k: [vals[k][t] for t in times] for k in ROLES},
        "synthetic": getattr(src, "name", None) == "synthetic" if src else _synthetic()}


def _synthetic() -> bool:
    from ..sources import timeseries

    return timeseries.provider().name == "synthetic"


def _score(m: dict) -> float:
    return round(m["manual_pct"] + (m["saturated_pct"] or 0) + min(100.0, 10 * (m["error_pct"] or 0))
                 + (OSCILLATION_SCORE if m["oscillating"] else 0), 1)


def health(conn, days: int = 7, until: str | None = None, src=None) -> dict:
    """Every loop on the loop list over the `days` up to `until`, worst first: the share of time in manual, the share of
    time in auto with the output saturated, the mean setpoint error in auto, and whether the value cycles regularly."""
    from . import loops as osc

    out, skipped = [], []
    for lp in loops(conn):
        try:
            s = series(conn, lp["id"], days, until, osc.STEP_MINUTES, src)
        except CalcError as e:
            skipped.append({"loop": lp["loop"], "why": str(e)})
            continue
        n = len(s["times"])
        if n < osc.MIN_READINGS:
            skipped.append({"loop": lp["loop"], "why": f"{n} readings, fewer than {osc.MIN_READINGS}"})
            continue
        auto = [i for i in range(n) if s["mode"][i] >= 0.5]
        sat = [i for i in auto if s["op"][i] >= 99 or s["op"][i] <= 1]
        err = [abs(s["pv"][i] - s["sp"][i]) / abs(s["sp"][i]) * 100 for i in auto if s["sp"][i]]
        c = osc.cycling(s["pv"], osc.STEP_MINUTES)
        m = {"loop": s["loop"], "id": s["id"], "description": s["description"], "holds": s["holds"], "unit": s["unit"],
             "manual_pct": round(100 * (n - len(auto)) / n, 1),
             "saturated_pct": round(100 * len(sat) / len(auto), 1) if auto else None,
             "error_pct": round(sum(err) / len(err), 2) if err else None,
             "oscillating": bool(c and osc._cycles(c)), "period_min": c["period_min"] if c and osc._cycles(c) else None,
             "readings": n, "points": s["points"]}
        m["flags"] = [f for f, on in (("manual", m["manual_pct"] >= MANUAL_PCT),
                                      ("saturated", (m["saturated_pct"] or 0) >= SATURATED_PCT),
                                      ("setpoint_error", (m["error_pct"] or 0) >= ERROR_PCT),
                                      ("oscillating", m["oscillating"])) if on]
        m["score"] = _score(m)
        out.append(m)
    out.sort(key=lambda m: (-m["score"], m["loop"]))
    start, end = window_of(conn, days, until)
    flagged = [m for m in out if m["flags"]]
    return {"since": start.isoformat(timespec="minutes"), "until": end.isoformat(timespec="minutes"), "loops": out,
            "skipped": skipped, "verdict": "no_loops" if not out else "flagged" if flagged else "healthy",
            "synthetic": _synthetic() if src is None else getattr(src, "name", None) == "synthetic",
            "thresholds": {"manual_pct": MANUAL_PCT, "saturated_pct": SATURATED_PCT, "error_pct": ERROR_PCT,
                           "oscillation_score": OSCILLATION_SCORE},
            "summary": {"loops": len(out), "flagged": [m["loop"] for m in flagged]},
            "source": "DCS loop list and each loop's PV, SP, OP and mode points",
            "note": (f"Worst first: score = % time in manual + % time saturated in auto + 10 x mean setpoint error (% of "
                     f"setpoint, at most 100) + {OSCILLATION_SCORE:g} for a regular oscillation. Flags at {MANUAL_PCT:g}% "
                     f"manual, {SATURATED_PCT:g}% saturated, {ERROR_PCT:g}% setpoint error. A worklist for review, not a "
                     "change request.")}

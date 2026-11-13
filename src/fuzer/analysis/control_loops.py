"""Control loops (#355): a loop from the DCS loop list as one thing: its controller tag, the item it holds, and the PI
points of its process value, setpoint, controller output and mode, read together on one time grid. The loop health
ranking and the stiction check build on this. Read-only: Fuzer never writes to the historian or the DCS.

    loop(conn, ref)                         the loop and its four points by role
    series(conn, ref, days, until, step)    its PV, SP, OP and mode over the window, aligned
"""

from .calcs import CalcError, read, window_of

ROLES = ("pv", "sp", "op", "mode")


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

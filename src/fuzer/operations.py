"""Asset-wide operations queries: production, deferment, constraints, wells, reliability, work and
turnarounds. The entity queries (queries.py) answer "tell me about this thing"; these answer "across the
asset, what matters": the questions surveillance engineers, reliability engineers, maintenance planners
and turnaround planners ask. All read-only, all compact (ranked summaries with the ids to drill into).

Dates are ISO strings. "Today" is the latest daily report in the data (`as_of`), so a historical or
fake asset reads as if it were current; pass as_of to look from another day.
"""

from collections import defaultdict
from datetime import date, timedelta

from . import queries
from .queries import _DOWNTIME

OPEN = ("CRTD", "REL", "OSNO", "OSTS")  # SAP: created, released, outstanding notification / task


def as_of(conn) -> str:
    row = conn.execute(
        """
        select max(g.value #>> '{}') as d from golden g join entity e on e.fuzer_id = g.fuzer_id
        where e.kind = 'daily_report' and g.field = 'report_date'
        """
    ).fetchone()
    return row["d"] or date.today().isoformat()


def _window(conn, since: str | None, until: str | None, days: int = 365) -> tuple[str, str]:
    until = until or (date.fromisoformat(as_of(conn)) + timedelta(days=1)).isoformat()
    since = since or (date.fromisoformat(until) - timedelta(days=days)).isoformat()
    return since, until


def _rows(conn, kind: str, where: str = "true", params: dict | None = None) -> list[dict]:
    """Every live entity of a kind as {id, label, f: golden fields}, filtered on f."""
    return conn.execute(
        f"""
        with x as (
            select e.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value) as f
            from entity e join golden g using (fuzer_id) left join entity_label l using (fuzer_id)
            where e.kind = %(kind)s and e.retired_at is null and e.merged_into is null
            group by e.fuzer_id, l.label
        )
        select * from x where {where}
        """,
        {"kind": kind, **(params or {})},
    ).fetchall()


def _tags_of(conn, ids: list[str]) -> dict[str, dict]:
    """The tag each notification / order / plan concerns (its position, else its equipment)."""
    out = {}
    for r in conn.execute(
        """
        select distinct on (c.src) c.src, c.dst, l.label, gd.value #>> '{}' as description
        from edge c join entity t on t.fuzer_id = c.dst
        left join entity_label l on l.fuzer_id = c.dst
        left join golden gd on gd.fuzer_id = c.dst and gd.field = 'description'
        where c.rel = 'concerns' and c.src = any(%s)
        order by c.src, (t.kind = 'tag') desc
        """,
        (ids,),
    ):
        out[r["src"]] = {"id": r["dst"], "label": r["label"], "description": r["description"]}
    return out


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- production ------------------------------------------------------------------------------------

def production_summary(conn, since: str | None = None, until: str | None = None, by: str = "month") -> dict:
    """Oil actual against potential (production efficiency), gas export, lift gas and flare per month or
    week, with deferred oil by reason from downtime. Default: the last 12 months."""
    since, until = _window(conn, since, until)
    days = _rows(conn, "daily_report", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s",
                 {"s": since, "u": until})
    period = (lambda d: d[:7]) if by == "month" else (
        lambda d: (date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat())
    agg = defaultdict(lambda: defaultdict(float))
    for r in days:
        p = agg[period(r["f"]["report_date"])]
        p["days"] += 1
        for k in ("oil_sm3", "oil_potential_sm3", "gas_export_ksm3", "lift_gas_ksm3", "flare_ksm3"):
            p[k] += _num(r["f"].get(k))
    lost = defaultdict(lambda: defaultdict(float))
    for r in conn.execute(_DOWNTIME + "select f from dd", {"id": None, "since": since, "until": until}):
        lost[period(r["f"]["start"][:10])][r["f"].get("reason") or "Unknown"] += _num(r["f"].get("lost_oil_sm3"))
    periods = []
    for k in sorted(agg):
        p = agg[k]
        pot = p["oil_potential_sm3"]
        periods.append({"period": k, "days": int(p["days"]), "oil_sm3": round(p["oil_sm3"]),
                        "oil_potential_sm3": round(pot) if pot else None,
                        "efficiency_pct": round(p["oil_sm3"] / pot * 100, 1) if pot else None,
                        "oil_sm3_per_day": round(p["oil_sm3"] / p["days"]),
                        "gas_export_ksm3": round(p["gas_export_ksm3"]), "lift_gas_ksm3_per_day":
                            round(p["lift_gas_ksm3"] / p["days"]), "flare_ksm3": round(p["flare_ksm3"]),
                        "deferred_oil_by_reason": {r: round(v) for r, v in
                                                   sorted(lost[k].items(), key=lambda x: -x[1])}})
    oil = sum(p["oil_sm3"] for p in periods)
    pot = sum(p["oil_potential_sm3"] or 0 for p in periods)
    return {"since": since, "until": until, "source": "EC DAY001 daily reports and EC downtime",
            "total": {"oil_sm3": oil, "oil_potential_sm3": pot or None,
                      "efficiency_pct": round(oil / pot * 100, 1) if pot else None},
            "periods": periods}


def deferment_pareto(conn, by: str = "reason", since: str | None = None, until: str | None = None,
                     limit: int = 15) -> dict:
    """Deferred (lost) oil ranked by reason, reason category, causing tag, its ISO 14224 class, or the
    well affected, with each group's share of the total. Default: the last 12 months."""
    since, until = _window(conn, since, until)
    params = {"id": None, "since": since, "until": until, "limit": limit}
    if by in ("reason", "category"):
        col = "reason" if by == "reason" else "reason_category"
        sql = f"""
            select coalesce(f->>'{col}', 'Unknown') as label, null::text as fuzer_id, count(*) as events,
                   sum((f->>'lost_oil_sm3')::numeric) as lost_oil_sm3, sum((f->>'duration_h')::numeric) as hours,
                   max(f->>'start') as last
            from dd group by 1 order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    elif by in ("tag", "class", "well"):
        rel = "affects" if by == "well" else "caused_by"
        via = "join edge k on k.src = c.dst and k.rel = 'classified_as'" if by == "class" else ""
        target = "k.dst" if by == "class" else "c.dst"
        well = "join entity w on w.fuzer_id = c.dst and w.kind = 'well'" if by == "well" else ""
        sql = f"""
            select l.label, {target} as fuzer_id, count(distinct dd.id) as events,
                   sum((dd.f->>'lost_oil_sm3')::numeric) as lost_oil_sm3, sum((dd.f->>'duration_h')::numeric) as hours,
                   max(dd.f->>'start') as last
            from dd join edge c on c.src = dd.id and c.rel = '{rel}' {well} {via}
            left join entity_label l on l.fuzer_id = {target}
            group by l.label, {target} order by lost_oil_sm3 desc nulls last limit %(limit)s
        """
    else:
        raise ValueError("by must be reason, category, tag, class or well")
    rows = conn.execute(_DOWNTIME + sql, params).fetchall()
    total = conn.execute(_DOWNTIME + "select coalesce(sum((f->>'lost_oil_sm3')::numeric), 0) as t from dd",
                         params).fetchone()["t"]
    total = float(total)
    return {"since": since, "until": until, "by": by, "total_lost_oil_sm3": round(total),
            "source": "EC downtime (deferment) records",
            "items": [{**r, "lost_oil_sm3": round(float(r["lost_oil_sm3"] or 0)), "hours": float(r["hours"] or 0),
                       "share_pct": round(float(r["lost_oil_sm3"] or 0) / total * 100, 1) if total else None}
                      for r in rows]}


def constraints(conn, since: str | None = None, until: str | None = None) -> dict:
    """What has been limiting production: losses by reason with the units that cause them, how often,
    how recently, and whether it is getting worse (second half of the window against the first).
    Default: the last 12 months."""
    since, until = _window(conn, since, until)
    mid = (date.fromisoformat(since) + (date.fromisoformat(until) - date.fromisoformat(since)) / 2).isoformat()
    params = {"id": None, "since": since, "until": until}
    rows = conn.execute(_DOWNTIME + """
        select dd.id, dd.f, array_remove(array_agg(distinct l.label), null) as caused_by,
               array_remove(array_agg(distinct c.dst), null) as caused_by_ids
        from dd left join edge c on c.src = dd.id and c.rel = 'caused_by'
        left join entity_label l on l.fuzer_id = c.dst
        group by dd.id, dd.f
    """, params).fetchall()
    out = defaultdict(lambda: {"events": 0, "lost_oil_sm3": 0.0, "lost_gas_ksm3": 0.0, "hours": 0.0,
                               "first_half_oil": 0.0, "second_half_oil": 0.0, "last": "", "units": defaultdict(int)})
    for r in rows:
        f = r["f"]
        g = out[(f.get("reason") or "Unknown", f.get("reason_category"))]
        oil = _num(f.get("lost_oil_sm3"))
        g["events"] += 1
        g["lost_oil_sm3"] += oil
        g["lost_gas_ksm3"] += _num(f.get("lost_gas_ksm3"))
        g["hours"] += _num(f.get("duration_h"))
        g["first_half_oil" if f["start"] < mid else "second_half_oil"] += oil
        g["last"] = max(g["last"], f["start"])
        for label, fid in zip(r["caused_by"], r["caused_by_ids"]):
            g["units"][(label, fid)] += 1
    items = []
    for (reason, cat), g in sorted(out.items(), key=lambda x: -x[1]["lost_oil_sm3"]):
        a, b = g.pop("first_half_oil"), g.pop("second_half_oil")
        units = g.pop("units")
        items.append({"reason": reason, "category": cat, **{k: (round(v) if isinstance(v, float) else v)
                                                             for k, v in g.items()},
                      "trend": "worse" if b > a * 1.25 else "better" if b < a * 0.75 else "steady",
                      "caused_by": [{"label": u[0], "id": u[1], "events": n}
                                    for u, n in sorted(units.items(), key=lambda x: -x[1])[:5]]})
    return {"since": since, "until": until, "split_at": mid, "source": "EC downtime records and their causes",
            "items": items}


def well_performance(conn, ref_id: str | None = None, since: str | None = None, limit: int = 20) -> dict:
    """Well tests: for one well, its test history; for the asset, wells ranked by oil rate lost between
    the first and latest test in the window, with water cut change and deferment. Default window: 12 months."""
    since, until = _window(conn, since, None)
    sql = """
        select t.dst as well, l.label as well_label, t.src as id, jsonb_object_agg(g.field, g.value) as f
        from edge t join entity w on w.fuzer_id = t.dst and w.kind = 'well'
        join golden g on g.fuzer_id = t.src
        left join entity_label l on l.fuzer_id = t.dst
        where t.rel = 'tests' and (%(well)s::text is null or t.dst = %(well)s)
        group by t.dst, l.label, t.src
    """
    tests = [r for r in conn.execute(sql, {"well": ref_id})
             if since <= (r["f"].get("test_date") or "") < until]
    by_well = defaultdict(list)
    for r in tests:
        by_well[(r["well"], r["well_label"])].append(r)
    keep = ("test_date", "oil_sm3d", "water_cut_pct", "gor_sm3_sm3", "lift_gas_ksm3d", "whp_barg")
    if ref_id:
        hist = sorted((r for rs in by_well.values() for r in rs), key=lambda r: r["f"]["test_date"])
        return {"since": since, "until": until, "source": "EC well tests",
                "tests": [{"id": r["id"], **{k: r["f"].get(k) for k in keep}} for r in hist]}
    lost = {r["fuzer_id"]: r["lost_oil_sm3"] for r in deferment_pareto(conn, "well", since, until, 500)["items"]}
    ranked = []
    for (well, label), rs in by_well.items():
        rs.sort(key=lambda r: r["f"]["test_date"])
        a, b = rs[0]["f"], rs[-1]["f"]
        q0, q1 = _num(a.get("oil_sm3d")), _num(b.get("oil_sm3d"))
        ranked.append({"well": label, "id": well, "tests": len(rs), "first_test": a.get("test_date"),
                       "latest_test": b.get("test_date"), "latest_test_id": rs[-1]["id"], "oil_sm3d_first": q0,
                       "oil_sm3d_latest": q1, "oil_change_pct": round((q1 - q0) / q0 * 100, 1) if q0 else None,
                       "water_cut_first": _num(a.get("water_cut_pct")), "water_cut_latest": _num(b.get("water_cut_pct")),
                       "lift_gas_ksm3d": _num(b.get("lift_gas_ksm3d")), "deferred_oil_sm3": lost.get(well, 0)})
    ranked.sort(key=lambda r: (r["oil_change_pct"] if r["oil_change_pct"] is not None else 0))
    return {"since": since, "until": until, "source": "EC well tests and EC downtime", "wells": len(ranked),
            "field_oil_sm3d_latest": round(sum(r["oil_sm3d_latest"] for r in ranked), 1),
            "biggest_declines": ranked[:limit],
            "water_cut_risers": sorted(ranked, key=lambda r: r["water_cut_first"] - r["water_cut_latest"])[:limit // 2]}


# --- reliability and maintenance -------------------------------------------------------------------

def bad_actors(conn, since: str | None = None, until: str | None = None, limit: int = 15) -> dict:
    """Equipment ranked by failures (corrective notifications), the downtime it caused and repeat
    failure modes, with MTBF; and MTBF by ISO 14224 class. Default window: 2 years."""
    since, until = _window(conn, since, until, days=730)
    notes = _rows(conn, "notification", "f->>'created' >= %(s)s and f->>'created' < %(u)s", {"s": since, "u": until})
    tags = _tags_of(conn, [n["id"] for n in notes])
    years = (date.fromisoformat(until) - date.fromisoformat(since)).days / 365.25
    per = defaultdict(lambda: {"failures": 0, "modes": defaultdict(int), "last": "", "notifications": []})
    for n in notes:
        t = tags.get(n["id"])
        if not t:
            continue
        p = per[(t["id"], t["label"], t["description"])]
        p["failures"] += 1
        p["modes"][n["f"].get("failure_mode_name") or n["f"].get("failure_mode") or "unknown"] += 1
        p["last"] = max(p["last"], n["f"].get("created") or "")
        p["notifications"].append(n["id"])
    caused = {r["fuzer_id"]: r for r in deferment_pareto(conn, "tag", since, until, 500)["items"]}
    classes = {r["src"]: (r["dst"], r["label"]) for r in conn.execute(
        """
        select c.src, c.dst, l.label from edge c left join entity_label l on l.fuzer_id = c.dst
        where c.rel = 'classified_as' and c.src = any(%s)
        """, ([k[0] for k in per],))}
    items, by_class = [], defaultdict(lambda: {"units": set(), "failures": 0})
    for (tid, label, desc), p in per.items():
        d = caused.get(tid, {})
        cls = classes.get(tid)
        repeat = {m: n for m, n in p["modes"].items() if n > 1}
        items.append({"id": tid, "tag": label, "description": desc, "class": cls[1] if cls else None,
                      "failures": p["failures"], "mtbf_days": round(years * 365.25 / p["failures"]),
                      "repeat_failure_modes": repeat, "last_failure": p["last"],
                      "downtime_events": d.get("events", 0), "lost_oil_sm3": d.get("lost_oil_sm3", 0)})
        if cls:
            by_class[cls]["units"].add(tid)
            by_class[cls]["failures"] += p["failures"]
    # downtime caused weighs most, then failures (one failure ~ 200 Sm3), then repeats
    items.sort(key=lambda r: (-(r["lost_oil_sm3"] or 0) - 200 * r["failures"], -sum(r["repeat_failure_modes"].values()),
                              r["tag"] or ""))
    return {"since": since, "until": until, "source": "SAP notifications (ISO 14224 failure modes), EC downtime",
            "bad_actors": items[:limit],
            "mtbf_by_class": sorted(({"class": c[1], "id": c[0], "units_with_failures": len(v["units"]),
                                      "failures": v["failures"],
                                      "mtbf_days_per_unit": round(years * 365.25 * len(v["units"]) / v["failures"])}
                                     for c, v in by_class.items()), key=lambda r: r["mtbf_days_per_unit"])}


def work_order_backlog(conn, as_of_date: str | None = None, limit: int = 20) -> dict:
    """Open work orders: counts by priority, order type and age, what is overdue (past its basic finish),
    the oldest items, and open notifications nobody has planned an order for yet."""
    today = as_of_date or as_of(conn)
    orders = _rows(conn, "work_order", "f->>'status' = any(%(o)s)", {"o": list(OPEN)})
    notes = _rows(conn, "notification", "f->>'status' = any(%(o)s)", {"o": list(OPEN)})
    planned = {r["dst"] for r in conn.execute(
        "select dst from edge where rel = 'for_notification' and dst = any(%s)", ([n["id"] for n in notes],))}
    tags = _tags_of(conn, [o["id"] for o in orders] + [n["id"] for n in notes])
    def age(d: str | None) -> int | None:
        return (date.fromisoformat(today) - date.fromisoformat(d)).days if d else None
    items = []
    for o in orders:
        f = o["f"]
        due = f.get("basic_finish")
        items.append({"id": o["id"], "order": o["label"], "type": f.get("order_type"), "status": f.get("status"),
                      "priority": f.get("priority"), "tag": (tags.get(o["id"]) or {}).get("label"),
                      "tag_id": (tags.get(o["id"]) or {}).get("id"), "text": f.get("text"),
                      "created": f.get("start"), "due": due, "age_days": age(f.get("start")),
                      "overdue_days": max(0, age(due)) if due and due < today else 0,
                      "revision": f.get("revision") or None})
    def count(key: str) -> dict:
        out = defaultdict(int)
        for i in items:
            out[str(i[key])] += 1
        return dict(sorted(out.items()))
    buckets = {"0-30 d": 0, "31-90 d": 0, "91-180 d": 0, "over 180 d": 0, "future": 0}
    for i in items:
        a = i["age_days"]
        buckets["future" if a is None or a < 0 else "0-30 d" if a <= 30 else "31-90 d" if a <= 90
                else "91-180 d" if a <= 180 else "over 180 d"] += 1
    overdue = sorted((i for i in items if i["overdue_days"]), key=lambda i: (i["priority"] or "9", -i["overdue_days"]))
    unplanned = [{"id": n["id"], "notification": n["label"], "created": n["f"].get("created"),
                  "priority": n["f"].get("priority"), "tag": (tags.get(n["id"]) or {}).get("label"),
                  "text": n["f"].get("short_text"), "failure_mode": n["f"].get("failure_mode_name")}
                 for n in notes if n["id"] not in planned]
    return {"as_of": today, "source": "SAP work orders and notifications", "open_orders": len(items),
            "by_priority": count("priority"), "by_type": count("type"), "by_age": buckets,
            "overdue": len(overdue), "overdue_items": overdue[:limit],
            "in_a_turnaround": sum(1 for i in items if i["revision"]),
            "oldest": sorted((i for i in items if i["age_days"] is not None and i["age_days"] >= 0),
                             key=lambda i: -i["age_days"])[:limit // 2],
            "notifications_without_order": sorted(unplanned, key=lambda n: (n["priority"] or "9", n["created"] or ""))}


def maintenance_compliance(conn, as_of_date: str | None = None, horizon_days: int = 90, limit: int = 25) -> dict:
    """Maintenance and inspection plans: overdue and due within the horizon, by kind (PM / INSP), with
    the share on time; tasks that need a shutdown are marked."""
    today = as_of_date or as_of(conn)
    horizon = (date.fromisoformat(today) + timedelta(days=horizon_days)).isoformat()
    plans = _rows(conn, "maintenance_plan")
    tags = _tags_of(conn, [p["id"] for p in plans])

    def item(p):
        f = p["f"]
        due = f.get("next_due") or ""
        return {"id": p["id"], "plan": p["label"], "kind": f.get("kind"), "task": f.get("task"),
                "tag": (tags.get(p["id"]) or {}).get("label"), "tag_id": (tags.get(p["id"]) or {}).get("id"),
                "last_done": f.get("last_done"), "next_due": due,
                "overdue_days": (date.fromisoformat(today) - date.fromisoformat(due)).days if due and due < today else 0,
                "needs_shutdown": f.get("needs_shutdown") == "X"}

    items = [item(p) for p in plans]
    summary = {}
    for kind in sorted({i["kind"] for i in items}):
        ks = [i for i in items if i["kind"] == kind]
        late = sum(1 for i in ks if i["overdue_days"])
        summary[kind] = {"plans": len(ks), "overdue": late, "on_time_pct": round((len(ks) - late) / len(ks) * 100, 1)}
    return {"as_of": today, "horizon": horizon, "source": "SAP maintenance and inspection plans", "summary": summary,
            "overdue": sorted((i for i in items if i["overdue_days"]), key=lambda i: -i["overdue_days"])[:limit],
            "due_soon": sorted((i for i in items if not i["overdue_days"] and today <= i["next_due"] <= horizon),
                               key=lambda i: i["next_due"])[:limit]}


def turnaround_scope(conn, revision: str | None = None, months_ahead: int = 18) -> dict:
    """Turnaround (shutdown) scope: work orders planned for a revision; overdue or upcoming plans that need
    the unit down; the worst bad actors not yet in scope; and spares for the scoped units that are out of
    stock (long lead items to order now)."""
    today = as_of(conn)
    orders = _rows(conn, "work_order", "coalesce(f->>'revision', '') <> ''")
    revisions = sorted({o["f"]["revision"] for o in orders})
    revision = revision or (revisions[0] if revisions else None)
    scoped = [o for o in orders if o["f"].get("revision") == revision]
    tags = _tags_of(conn, [o["id"] for o in scoped])
    work = [{"id": o["id"], "order": o["label"], "tag": (tags.get(o["id"]) or {}).get("label"),
             "tag_id": (tags.get(o["id"]) or {}).get("id"), "priority": o["f"].get("priority"),
             "activity": o["f"].get("activity_name"), "text": o["f"].get("text"), "planned_start": o["f"].get("start"),
             "status": o["f"].get("status")} for o in scoped]
    start = min((w["planned_start"] for w in work if w["planned_start"]), default=None)
    until = (date.fromisoformat(start or today) + timedelta(days=30 * months_ahead)).isoformat()
    in_scope = {w["tag_id"] for w in work}
    comp = maintenance_compliance(conn, today, horizon_days=(date.fromisoformat(until) - date.fromisoformat(today)).days,
                                  limit=500)
    shutdown_plans = [p for p in comp["overdue"] + comp["due_soon"] if p["needs_shutdown"]]
    actors = [a for a in bad_actors(conn, limit=30)["bad_actors"] if a["id"] not in in_scope][:5]
    spares = []
    for tid in in_scope:
        if not tid:
            continue
        for p in queries.spares(conn, tid):
            if not p.get("stock"):
                spares.append({"for": next(w["tag"] for w in work if w["tag_id"] == tid), "material": p["label"],
                               "id": p["fuzer_id"], "description": p["description"], "mpn": p.get("mpn"), "stock": p["stock"]})
    return {"as_of": today, "revision": revision, "revisions": revisions, "planned_start": start,
            "source": "SAP work orders (revision), maintenance plans, notifications, BOMs and stock",
            "scoped_work": work,
            "shutdown_plans_not_in_scope": [p for p in shutdown_plans if p["tag_id"] not in in_scope],
            "shutdown_plans_in_scope": [p for p in shutdown_plans if p["tag_id"] in in_scope],
            "bad_actors_not_in_scope": actors, "spares_out_of_stock": spares}


# --- gas lift --------------------------------------------------------------------------------------

def _response(tests: list[dict]) -> float | None:
    """Oil gained per extra lift gas (Sm3/d oil per kSm3/d gas) from a well's tests: the slope of rate on
    injection after taking out the time trend (decline), so months of decline don't read as no response."""
    pts = [(i, _num(t["oil_sm3d"]), _num(t["lift_gas_ksm3d"])) for i, t in enumerate(tests) if t.get("oil_sm3d")]
    if len(pts) < 5:
        return None

    def detrend(ys):
        ts = [p[0] for p in pts]
        mt, my = sum(ts) / len(ts), sum(ys) / len(ys)
        vt = sum((t - mt) ** 2 for t in ts)
        b = sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / vt if vt else 0.0
        return [y - my - b * (t - mt) for t, y in zip(ts, ys)]

    q, g = detrend([p[1] for p in pts]), detrend([p[2] for p in pts])
    vg = sum(x * x for x in g)
    return round(sum(a * b for a, b in zip(q, g)) / vg, 2) if vg > 1e-6 else None


def gas_lift_network(conn, ref_id: str | None = None, since: str | None = None, limit: int = 15) -> dict:
    """The gas lift network as it stands: lift gas sources (compression trains, the bypass) -> headers ->
    jacket gas lift manifolds (some fed through another jacket's manifold) -> gas lifted wells; per header
    the jackets, wells, lift gas and oil from the latest well tests; per well its lift gas, oil per lift gas
    and measured response to more lift gas (from its tests over the window, default 12 months).
    With ref_id (a header, manifold, jacket, compressor or well): only the part of the network it is in."""
    since, until = _window(conn, since, None)
    rows = conn.execute(
        """
        select e.src, e.dst, ls.label as src_label, ld.label as dst_label, s.kind as src_kind, d.kind as dst_kind,
               ts.value #>> '{}' as src_type, td.value #>> '{}' as dst_type,
               ds.value #>> '{}' as src_desc, dd.value #>> '{}' as dst_desc
        from edge e join entity s on s.fuzer_id = e.src join entity d on d.fuzer_id = e.dst
        left join entity_label ls on ls.fuzer_id = e.src left join entity_label ld on ld.fuzer_id = e.dst
        left join golden ts on ts.fuzer_id = e.src and ts.field = 'ec_type'
        left join golden td on td.fuzer_id = e.dst and td.field = 'ec_type'
        left join golden ds on ds.fuzer_id = e.src and ds.field = 'description'
        left join golden dd on dd.fuzer_id = e.dst and dd.field = 'description'
        where e.rel = 'lift_gas_to' and e.valid_to is null
        """
    ).fetchall()
    if not rows:
        return {"error": "no gas lift network in the data (Energy Components lift gas edges)"}
    parents, children, info = defaultdict(list), defaultdict(list), {}
    for r in rows:
        parents[r["dst"]].append(r["src"])
        children[r["src"]].append(r["dst"])
        info[r["src"]] = {"label": r["src_label"], "kind": r["src_kind"], "type": r["src_type"], "description": r["src_desc"]}
        info[r["dst"]] = {"label": r["dst_label"], "kind": r["dst_kind"], "type": r["dst_type"], "description": r["dst_desc"]}
    sites = {r["src"]: {"id": r["dst"], "label": r["label"], "description": r["description"]} for r in conn.execute(
        """
        select e.src, e.dst, l.label, g.value #>> '{}' as description
        from edge e left join entity_label l on l.fuzer_id = e.dst
        left join golden g on g.fuzer_id = e.dst and g.field = 'description'
        where e.rel = 'located_at' and e.src = any(%s)
        """, (list(info),))}

    def supply(node: str) -> list[str]:
        """Upstream of a node: its manifolds (one feed each) up to its header, then everything feeding
        that header (other headers, compression trains, the bypass), nearest first."""
        path, seen, todo = [], {node}, [node]
        while todo:
            for p in parents.get(todo.pop(0), []):
                if p not in seen:
                    seen.add(p)
                    path.append(p)
                    todo.append(p)
        return path

    def own_header(node: str) -> str | None:
        return next((n for n in supply(node) if info[n]["type"] == "header"), None)

    def jacket(manifold: str) -> str:
        s = sites.get(manifold) or {}
        return f"{s['label']} ({s['description']})" if s.get("description") else s.get("label") or info[manifold]["label"]

    def below(node: str) -> set[str]:
        out, todo = set(), [node]
        while todo:
            for c in children.get(todo.pop(), []):
                if c not in out:
                    out.add(c)
                    todo.append(c)
        return out

    wells = [n for n, i in info.items() if i["kind"] == "well"]
    tests = defaultdict(list)
    for r in conn.execute(
        """
        select t.dst as well, jsonb_object_agg(g.field, g.value) as f
        from edge t join golden g on g.fuzer_id = t.src
        where t.rel = 'tests' and t.dst = any(%s) group by t.src, t.dst
        """, (wells,)):
        if since <= (r["f"].get("test_date") or "") < until:
            tests[r["well"]].append(r["f"])
    per_well = []
    for w in wells:
        ts = sorted(tests[w], key=lambda t: t["test_date"])
        path = supply(w)
        manifolds = [n for n in path if info[n]["type"] == "manifold"]
        header = own_header(w)
        last = ts[-1] if ts else {}
        gas, oil = _num(last.get("lift_gas_ksm3d")), _num(last.get("oil_sm3d"))
        per_well.append({
            "well": info[w]["label"], "id": w, "header": info[header]["label"] if header else None, "header_id": header,
            "jacket": jacket(manifolds[0]) if manifolds else None,
            "through_jackets": [jacket(m) for m in manifolds[1:]],
            "latest_test": last.get("test_date"), "lift_gas_ksm3d": gas, "oil_sm3d": oil,
            "water_cut_pct": _num(last.get("water_cut_pct")) if last else None,
            "oil_per_lift_gas": round(oil / gas, 2) if gas else None,
            "response_oil_per_extra_gas": _response(ts), "tests": len(ts)})
    if ref_id:
        scope = {ref_id} | below(ref_id) | set(supply(ref_id))
        scope |= {m for m, s in sites.items() if s["id"] == ref_id}  # a jacket: its manifolds
        scope |= {x for m in list(scope) if sites.get(m, {}).get("id") == ref_id for x in below(m)}
        per_well = [w for w in per_well if w["id"] in scope]
    headers = []
    for h in sorted({w["header_id"] for w in per_well if w["header_id"]}, key=lambda h: info[h]["label"] or ""):
        ws = [w for w in per_well if w["header_id"] == h]
        manifolds = [n for n in below(h) if info[n]["type"] == "manifold" and own_header(n) == h]
        gas = round(sum(w["lift_gas_ksm3d"] for w in ws), 1)
        oil = round(sum(w["oil_sm3d"] for w in ws), 1)
        headers.append({"header": info[h]["label"], "description": info[h]["description"], "id": h,
                        "fed_by": [info[p]["label"] for p in parents.get(h, [])],
                        "jackets": sorted(jacket(m) for m in manifolds),
                        "jackets_through_another_jacket": sum(1 for m in manifolds if info[parents[m][0]]["type"] == "manifold"),
                        "gas_lifted_wells": len(ws), "lift_gas_ksm3d": gas, "oil_sm3d": oil,
                        "oil_per_lift_gas": round(oil / gas, 2) if gas else None})
    rated = [w for w in per_well if w["response_oil_per_extra_gas"] is not None and w["lift_gas_ksm3d"]]
    out = {"since": since, "until": until, "source": "EC lift gas network and well tests",
           "headers": headers, "gas_lifted_wells": len(per_well),
           "lift_gas_ksm3d": round(sum(w["lift_gas_ksm3d"] for w in per_well), 1),
           "least_response": sorted(rated, key=lambda w: w["response_oil_per_extra_gas"])[:limit],
           "most_response": sorted(rated, key=lambda w: -w["response_oil_per_extra_gas"])[:limit],
           "lifting_mostly_water": sorted((w for w in per_well if (w["water_cut_pct"] or 0) >= 70),
                                          key=lambda w: -(w["water_cut_pct"] or 0))[:limit]}
    if ref_id:
        out["supply_path"] = [{"id": n, "label": info[n]["label"], "description": info[n]["description"],
                               "type": info[n]["type"], "located_at": (sites.get(n) or {}).get("label")}
                              for n in supply(ref_id)]
        out["wells"] = per_well
    return out

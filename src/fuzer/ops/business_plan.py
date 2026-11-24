"""The business plan against actual (step 9 of docs/work-model.md): intent lines with estimates under a plan container
(a business plan or a program), each matched to the real cases that do the work.

    add_line(conn, container_id, title, by, ...)   an intent line: what the plan means to do, where, when, for how much
    update_line(conn, line_id, by, **fields)       change it, or drop it (status 'dropped')
    match(conn, line_id, item_id, by)              this case does that line's work (a case counts against one line)
    unmatch(conn, item_id, by)
    auto_match(conn, container_id, by)             cases whose jobs are on a line's assets in its period, matched
    against_actual(conn, container_id)             each line's estimate against its cases' actuals, and the unplanned

A line is an intent need: nothing raises it, a planner writes it. Its actuals are read from its cases' jobs, never
stored: hours worked on done jobs, hours still booked on open ones, and the cost SAP records on their orders. Work done
in the plan's containers that no line covers is the unplanned work, the other half of plan against actual.
"""

from datetime import date

from ..core import events

FIELDS = ("title", "kind", "subjects", "period_start", "period_end", "estimate_hours", "estimate_cost",
          "estimate_value_usd", "owner", "note", "status")
STATUSES = ("open", "dropped")


class PlanLineError(ValueError):
    pass


def _line(conn, line_id: int) -> dict:
    row = conn.execute("select * from work_plan_line where id = %s", (line_id,)).fetchone()
    if row is None:
        raise PlanLineError(f"no plan line {line_id}")
    return row


def _period(start, end) -> None:
    if start and end and date.fromisoformat(str(end)[:10]) < date.fromisoformat(str(start)[:10]):
        raise PlanLineError("a line's period ends after it starts")


def add_line(conn, container_id: int, title: str, by: str, kind: str | None = None, subjects=(),
             period_start=None, period_end=None, estimate_hours: float | None = None,
             estimate_cost: float | None = None, estimate_value_usd: float | None = None, owner: str | None = None,
             note: str | None = None) -> dict:
    if not (title or "").strip():
        raise PlanLineError("a plan line needs a title")
    if conn.execute("select 1 from work_container where id = %s", (container_id,)).fetchone() is None:
        raise PlanLineError(f"no container {container_id}")
    _period(period_start, period_end)
    for v in (estimate_hours, estimate_cost):
        if v is not None and v < 0:
            raise PlanLineError("an estimate is 0 or more")
    row = conn.execute(
        """
        insert into work_plan_line (container_id, title, kind, subjects, period_start, period_end, estimate_hours,
                                    estimate_cost, estimate_value_usd, owner, note, created_by, updated_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (container_id, title.strip(), kind, list(subjects), period_start, period_end, estimate_hours,
              estimate_cost, estimate_value_usd, owner, note, by, by)).fetchone()
    events.publish(conn, "plan_line.changed", f"plan_line:{row['id']}",
                   {"id": row["id"], "container": container_id, "change": "added", "by": by})
    return row


def update_line(conn, line_id: int, by: str, **fields) -> dict:
    bad = set(fields) - set(FIELDS)
    if bad:
        raise PlanLineError(f"can't change {sorted(bad)}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise PlanLineError(f"a line's status is one of {', '.join(STATUSES)}")
    row = _line(conn, line_id)
    _period(fields.get("period_start", row["period_start"]), fields.get("period_end", row["period_end"]))
    if not fields:
        return row
    if "subjects" in fields:
        fields["subjects"] = list(fields["subjects"] or [])
    row = conn.execute("update work_plan_line set " + ", ".join(f"{k} = %({k})s" for k in fields) +
                       ", updated_by = %(by)s, updated_at = now() where id = %(id)s returning *",
                       {**fields, "by": by, "id": line_id}).fetchone()
    events.publish(conn, "plan_line.changed", f"plan_line:{line_id}",
                   {"id": line_id, "change": "updated", "fields": sorted(fields), "by": by})
    return row


def match(conn, line_id: int, item_id: int, by: str, how: str = "person") -> dict:
    """This case does that line's work. A case counts against one line: matching it again moves it."""
    _line(conn, line_id)
    if conn.execute("select 1 from work_item where id = %s", (item_id,)).fetchone() is None:
        raise PlanLineError(f"no case {item_id}")
    row = conn.execute(
        """
        insert into work_plan_match (item_id, line_id, how, matched_by) values (%s, %s, %s, %s)
        on conflict (item_id) do update set line_id = excluded.line_id, how = excluded.how,
            matched_by = excluded.matched_by, matched_at = now()
        returning *
        """, (item_id, line_id, how, by)).fetchone()
    events.publish(conn, "plan_line.changed", f"plan_line:{line_id}",
                   {"id": line_id, "change": "matched", "case": item_id, "how": how, "by": by})
    return row


def unmatch(conn, item_id: int, by: str) -> bool:
    row = conn.execute("delete from work_plan_match where item_id = %s returning line_id", (item_id,)).fetchone()
    if row:
        events.publish(conn, "plan_line.changed", f"plan_line:{row['line_id']}",
                       {"id": row["line_id"], "change": "unmatched", "case": item_id, "by": by})
    return row is not None


def _tree(conn, container_id: int) -> list[int]:
    """The container and every container under it."""
    return [r["id"] for r in conn.execute(
        """
        with recursive t as (select id from work_container where id = %s
                             union select c.id from work_container c join t on c.parent_id = t.id)
        select id from t
        """, (container_id,))]


def auto_match(conn, container_id: int, by: str = "fuzer") -> dict:
    """Each open line with assets takes the cases whose jobs are on those assets, in its period and of its kind,
    that no line has yet. A person's match is never moved."""
    matched = 0
    for line in conn.execute("select * from work_plan_line where container_id = %s and status = 'open' "
                             "and cardinality(subjects) > 0 order by id", (container_id,)).fetchall():
        for r in conn.execute(
            """
            select distinct p.item_id from plan_item p
            where p.item_id is not null and p.subjects && %(subjects)s and p.status <> 'cancelled'
              and (%(kind)s::text is null or p.kind = %(kind)s)
              and (%(start)s::date is null or p.end_at >= %(start)s::date)
              and (%(end)s::date is null or p.start_at < %(end)s::date + 1)
              and not exists (select 1 from work_plan_match m where m.item_id = p.item_id)
            """, {"subjects": list(line["subjects"]), "kind": line["kind"], "start": line["period_start"],
                  "end": line["period_end"]}).fetchall():
            match(conn, line["id"], r["item_id"], by, how="auto")
            matched += 1
    return {"matched": matched}


def _hours(job: dict) -> float:
    if job["crew_hours"] is not None:
        return float(job["crew_hours"])
    if job["duration_hours"] is not None:
        return float(job["duration_hours"])
    return (job["end_at"] - job["start_at"]).total_seconds() / 3600


def _order_cost(conn, job_ids: list[int]) -> float | None:
    """The cost SAP records on the orders behind these jobs, through their source links."""
    row = conn.execute(
        """
        select sum((g.value #>> '{}')::numeric) as cost, count(g.value) as n
        from work_source_link l join entity_key k on k.kind = 'work_order' and k.key = l.key
        join golden g on g.fuzer_id = k.fuzer_id and g.field = 'cost' and jsonb_typeof(g.value) = 'number'
        where l.object = 'job' and l.system = 'sap' and l.object_type = 'order' and l.object_id = any(%s)
        """, (job_ids,)).fetchone()
    return float(row["cost"]) if row and row["n"] else None


def _actuals(conn, case_ids: list[int]) -> dict:
    jobs = conn.execute("select * from plan_item where item_id = any(%s) and status <> 'cancelled'",
                        (case_ids,)).fetchall() if case_ids else []
    states = [r["state"] for r in conn.execute("select state from work_item where id = any(%s)", (case_ids,))] \
        if case_ids else []
    return {
        "cases": len(case_ids), "cases_closed": sum(s in ("done", "cancelled") for s in states),
        "jobs": len(jobs), "jobs_done": sum(j["status"] == "done" for j in jobs),
        "hours_done": round(sum(_hours(j) for j in jobs if j["status"] == "done"), 1),
        "hours_booked": round(sum(_hours(j) for j in jobs if j["status"] != "done"), 1),
        "cost": _order_cost(conn, [j["id"] for j in jobs]) if jobs else None,
    }


def against_actual(conn, container_id: int) -> dict:
    """Each line's estimate against its cases' actuals, the plan's totals, and the unplanned: cases with jobs in the
    plan's containers that no line covers."""
    row = conn.execute("select * from work_container where id = %s", (container_id,)).fetchone()
    if row is None:
        raise PlanLineError(f"no container {container_id}")
    lines = []
    for line in conn.execute("select * from work_plan_line where container_id = %s order by period_start nulls last, id",
                             (container_id,)).fetchall():
        cases = [r["item_id"] for r in conn.execute("select item_id from work_plan_match where line_id = %s",
                                                    (line["id"],))]
        a = _actuals(conn, cases)
        state = "dropped" if line["status"] == "dropped" else "not started" if not cases else \
            "done" if a["cases_closed"] == a["cases"] else "in progress"
        est = line["estimate_hours"]
        lines.append({**line, "actual": a, "state": state, "case_ids": cases,
                      "hours_variance": round(a["hours_done"] + a["hours_booked"] - est, 1) if est is not None else None,
                      "cost_variance": round(a["cost"] - line["estimate_cost"], 2)
                      if a["cost"] is not None and line["estimate_cost"] is not None else None})
    tree = _tree(conn, container_id)
    planned = {c for line in lines for c in line["case_ids"]}
    in_tree = {r["item_id"] for r in conn.execute(
        "select distinct item_id from plan_item where item_id is not null and container_id = any(%s)", (tree,))}
    in_tree |= {r["item_id"] for r in conn.execute(
        "select item_id from work_container_case where container_id = any(%s)", (tree,))}
    governing = {r["item_id"] for r in conn.execute("select item_id from work_container where id = any(%s) "
                                                    "and item_id is not null", (tree,))}
    unplanned_ids = sorted(in_tree - planned - governing)
    unplanned = [dict(c) for c in conn.execute("select id, title, state, type from work_item where id = any(%s) "
                                               "order by id", (unplanned_ids,))]
    open_lines = [x for x in lines if x["status"] == "open"]
    totals = {"lines": len(open_lines), "estimate_hours": sum(x["estimate_hours"] or 0 for x in open_lines),
              "estimate_cost": sum(x["estimate_cost"] or 0 for x in open_lines),
              "hours_done": round(sum(x["actual"]["hours_done"] for x in open_lines), 1),
              "hours_booked": round(sum(x["actual"]["hours_booked"] for x in open_lines), 1),
              "not_started": sum(x["state"] == "not started" for x in open_lines),
              "done": sum(x["state"] == "done" for x in open_lines),
              "unplanned_cases": len(unplanned), "unplanned": _actuals(conn, unplanned_ids)}
    return {"container": {"id": row["id"], "kind": row["kind"], "title": row["title"]}, "lines": lines,
            "totals": totals, "unplanned": unplanned}

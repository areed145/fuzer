"""Schedules on the integrated plan (P5.18, #1170): a turnaround, project or campaign whose activities are plan items
with durations, links between them and the shared resources they need.

    seed(conn, by)                     import the active project's bundle schedule templates that aren't on the plan yet
    import_template(conn, doc, by)     one template (a planner's first layout, or a P6 export turned into one)
    projects(conn)                     the schedules on the plan
    analyse(conn, code)                the critical path (forward and backward pass, total float) and the schedule
                                       levelled against every shared resource, other planned work's use included
    apply(conn, code, by)              write the levelled dates onto the plan items (P6's "schedule")
    add_activity / change_activity / remove_activity / link / unlink   what a planner edits on Plan
    set_demand / set_resource          the resources an activity needs, and what a resource has

The activities are ordinary plan items (kind turnaround, project or campaign) with a project, an activity code and a
duration, so clashes, bundling, parts readiness and SIMOPS read them like any other planned job, and a crew's other
planned work (an SAP order on the same work center) counts against the same capacity when levelling.

Time inside the engine is working hours from the project's start on its calendar (hours a day from the start's
time of day, days a week from its weekday); dates are worked out from that. The pure functions `critical_path`
and `level` take plain data, so the logic is tested without a database.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from ..core import clock, events
from ..core.plan import STATUSES, PlanError
from . import plan

TYPES = ("FS", "SS", "FF", "SF")
KINDS = ("turnaround", "project", "campaign")
RESOURCE_KINDS = ("crew", "beds", "seats", "vessel", "crane", "deck", "capacity", "other")
EPS = 1e-6


# ---- the calendar ----------------------------------------------------------------------------------------------

class Calendar:
    """Working hours from a start: `hours_per_day` from the start's time of day, on `days_per_week` days counted
    from the start's weekday (5 skips the two days after the fifth)."""

    def __init__(self, start: datetime, hours_per_day: float = 24, days_per_week: int = 7):
        self.start, self.hpd, self.dpw = start, float(hours_per_day), int(days_per_week)

    def _working(self, day: int) -> bool:
        return self.dpw >= 7 or day % 7 < self.dpw

    def at(self, hours: float) -> datetime:
        """The date and time `hours` working hours after the start."""
        if self.hpd >= 24 and self.dpw >= 7:
            return self.start + timedelta(hours=hours)
        day, left = 0, max(0.0, hours)
        while True:
            if self._working(day):
                if left <= self.hpd + EPS:  # ends on this day (at its close when exactly a day's work is left)
                    return self.start + timedelta(days=day, hours=min(left, self.hpd))
                left -= self.hpd
            day += 1

    def hours(self, when: datetime) -> float:
        """Working hours from the start to `when` (negative before it)."""
        if self.hpd >= 24 and self.dpw >= 7:
            return (when - self.start).total_seconds() / 3600
        if when <= self.start:
            return -(self.start - when).total_seconds() / 3600 * self.hpd / 24 * self.dpw / 7
        delta = when - self.start
        days, rest = delta.days, delta.seconds / 3600
        full = sum(self.hpd for d in range(days) if self._working(d))
        return full + (min(rest, self.hpd) if self._working(days) else 0.0)


# ---- the pure engine --------------------------------------------------------------------------------------------

def _order(keys: list, links: list[tuple]) -> list:
    """Activities in an order where every predecessor comes first; a loop is refused, naming it."""
    succs, indeg = defaultdict(list), {k: 0 for k in keys}
    for p, s, *_ in links:
        succs[p].append(s)
        indeg[s] += 1
    ready = [k for k in keys if indeg[k] == 0]
    out = []
    while ready:
        k = ready.pop(0)
        out.append(k)
        for s in succs[k]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
    if len(out) < len(keys):
        stuck = sorted(str(k) for k in keys if indeg[k] > 0)
        raise PlanError(f"the logic loops back on itself through {', '.join(stuck[:6])}: remove one of those links")
    return out


def _earliest(d: dict, p, s, kind: str, lag: float, es: dict, ef: dict) -> float:
    """The earliest start of s that one link allows."""
    return {"FS": ef[p] + lag, "SS": es[p] + lag, "FF": ef[p] + lag - d[s], "SF": es[p] + lag - d[s]}[kind]


def critical_path(durations: dict, links: list[tuple], not_before: dict | None = None, finish_by: float | None = None) -> dict:
    """Forward and backward pass. durations: {activity: working hours}; links: [(pred, succ, type, lag)];
    not_before: {activity: earliest start}. Returns per activity es, ef, ls, lf and total float (hours), the
    critical activities (float ≤ 0 against the finish, or against `finish_by` when that is later is not the case)
    and the critical chain, first to last."""
    keys = list(durations)
    links = [(p, s, t or "FS", float(lag or 0)) for p, s, t, lag in links if p in durations and s in durations]
    order = _order(keys, links)
    preds, succs = defaultdict(list), defaultdict(list)
    for p, s, t, lag in links:
        preds[s].append((p, t, lag))
        succs[p].append((s, t, lag))
    d = {k: max(0.0, float(v or 0)) for k, v in durations.items()}
    es, ef = {}, {}
    for k in order:
        es[k] = max([(not_before or {}).get(k, 0.0), 0.0] + [_earliest(d, p, k, t, lag, es, ef) for p, t, lag in preds[k]])
        ef[k] = es[k] + d[k]
    finish = max(ef.values(), default=0.0)
    end = max(finish, finish_by) if finish_by is not None else finish
    ls, lf = {}, {}
    for k in reversed(order):
        lates = [end]
        for s, t, lag in succs[k]:
            lates.append({"FS": ls[s] - lag, "SS": ls[s] - lag + d[k], "FF": lf[s] - lag, "SF": lf[s] - lag + d[k]}[t])
        lf[k] = min(lates)
        ls[k] = lf[k] - d[k]
    total = {k: ls[k] - es[k] for k in keys}
    least = min(total.values(), default=0.0)
    critical = [k for k in order if total[k] <= least + EPS]
    # The chain: from the activity that finishes last, back through the predecessor that drives each start.
    chain = []
    k = max((k for k in critical), key=lambda k: (ef[k], -order.index(k)), default=None)
    while k is not None:
        chain.append(k)
        driving = [p for p, t, lag in preds[k] if p in critical and abs(_earliest(d, p, k, t, lag, es, ef) - es[k]) <= EPS]
        k = driving[0] if driving else None
    return {"order": order, "es": es, "ef": ef, "ls": ls, "lf": lf, "float": total, "finish": finish,
            "critical": critical, "chain": chain[::-1]}


def level(durations: dict, links: list[tuple], demands: dict, capacity, background: dict | None = None,
          not_before: dict | None = None, horizon: float | None = None) -> dict:
    """A serial schedule: activities in order of least late start (from the critical path), each started at the first
    hour its logic allows where every resource it needs has room for the whole of it. demands: {activity: {resource:
    quantity}}; capacity(resource, hour) -> amount; background: {resource: [(from, to, quantity)]} other planned
    work using it. Returns per activity its levelled start and finish, the hours resources held it back and which,
    the levelled finish and the chain that sets it (logic or the resource that made each wait)."""
    cp = critical_path(durations, links, not_before)
    d = {k: max(0.0, float(v or 0)) for k, v in durations.items()}
    links = [(p, s, t or "FS", float(lag or 0)) for p, s, t, lag in links if p in d and s in d]
    preds = defaultdict(list)
    for p, s, t, lag in links:
        preds[s].append((p, t, lag))
    span = int(horizon or (sum(d.values()) + cp["finish"] + 24 * 7)) + 1
    used: dict[str, list[float]] = defaultdict(lambda: [0.0] * span)
    for r, rows in (background or {}).items():
        for a, b, q in rows:
            for h in range(max(0, int(a)), min(span, int(-(-b // 1)))):
                used[r][h] += q
    start, finish, waited, why = {}, {}, {}, {}
    holders: dict[str, list[list]] = defaultdict(lambda: [[] for _ in range(span)])
    rank = {k: i for i, k in enumerate(cp["order"])}
    pending = set(d)
    while pending:
        ready = [k for k in pending if all(p in start for p, _, _ in preds[k])]
        k = min(ready, key=lambda k: (cp["ls"][k], cp["float"][k], rank[k]))
        pending.discard(k)
        logic = max([(not_before or {}).get(k, 0.0), 0.0] + [_earliest(d, p, k, t, lag, start, finish) for p, t, lag in preds[k]])
        t = logic
        need = {r: q for r, q in (demands.get(k) or {}).items() if q}
        blocked_by = None
        while True:
            hours = range(int(t), int(-(-(t + d[k]) // 1)) if d[k] else int(t))
            clash = next(((r, h) for h in hours for r, q in need.items() if h < span and used[r][h] + q > capacity(r, h) + EPS), None)
            if clash is None or t > span:
                break
            blocked_by = clash
            t = clash[1] + 1.0
        start[k], finish[k] = t, t + d[k]
        waited[k] = t - logic
        if waited[k] > EPS and blocked_by:
            r, h = blocked_by
            why[k] = {"resource": r, "held_by": sorted({x for x in holders[r][min(h, span - 1)] if x != k})}
        for h in range(int(t), min(span, int(-(-(t + d[k]) // 1)))):
            for r, q in need.items():
                used[r][h] += q
                holders[r][h].append(k)
    end = max(finish.values(), default=0.0)
    chain, k = [], max(finish, key=lambda x: (finish[x], -rank[x]), default=None)
    while k is not None:
        step = {"activity": k}
        driving = [p for p, t, lag in preds[k] if abs(_earliest(d, p, k, t, lag, start, finish) - start[k]) <= EPS]
        if waited[k] > EPS and k in why:
            step["waited_for"] = why[k]["resource"]
            nxt = why[k]["held_by"]
            k2 = max(nxt, key=lambda x: finish[x]) if nxt else None
        else:
            k2 = driving[0] if driving else None
        chain.append(step)
        k = k2 if k2 not in [c["activity"] for c in chain] else None
    return {"start": start, "finish": finish, "waited": waited, "why": why, "end": end, "chain": chain[::-1],
            "critical_path": cp}


def overloads(durations: dict, starts: dict, demands: dict, capacity, background: dict | None = None) -> list[dict]:
    """Where the schedule given (every activity at `starts`) needs more of a resource than it has: one row per run of
    hours over capacity, with the peak need, the capacity and the activities using it then."""
    use: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for k, s in starts.items():
        for r, q in (demands.get(k) or {}).items():
            for h in range(int(s), int(-(-(s + durations[k]) // 1))):
                use[r][h].append((k, q))
    for r, rows in (background or {}).items():
        for a, b, q in rows:
            for h in range(max(0, int(a)), int(-(-b // 1))):
                use[r][h].append((None, q))
    out = []
    for r, hours in use.items():
        run = None
        for h in sorted(hours):
            need = sum(q for _, q in hours[h])
            cap = capacity(r, h)
            if need > cap + EPS:
                if run and run["to"] == h:
                    run["to"] = h + 1
                    run["peak"] = max(run["peak"], need)
                    run["activities"] |= {k for k, _ in hours[h] if k}
                    run["other_work"] = run["other_work"] or any(k is None for k, _ in hours[h])
                else:
                    run and out.append(run)
                    run = {"resource": r, "from": h, "to": h + 1, "peak": need, "capacity": cap,
                           "activities": {k for k, _ in hours[h] if k}, "other_work": any(k is None for k, _ in hours[h])}
        run and out.append(run)
    for o in out:
        o["activities"] = sorted(o["activities"])
    return sorted(out, key=lambda o: (o["from"], o["resource"]))


def profile(durations: dict, starts: dict, demands: dict, capacity, background: dict | None = None,
            end: float | None = None, step: float = 24) -> dict:
    """A resource histogram: per resource, per `step` working hours from the start, the peak use by this schedule
    (activities at `starts`), the peak use by other planned work and the least capacity in that step."""
    span = int(-(-(end if end is not None else max([starts[k] + durations[k] for k in starts] or [0])) // 1))
    out = {}
    for r in sorted({r for d in demands.values() for r, q in (d or {}).items() if q}):
        mine, other = [0.0] * span, [0.0] * span
        for k, s in starts.items():
            q = (demands.get(k) or {}).get(r) or 0
            for h in range(max(0, int(s)), min(span, int(-(-(s + durations[k]) // 1)))):
                mine[h] += q
        for a, b, q in (background or {}).get(r, ()):
            for h in range(max(0, int(a)), min(span, int(-(-b // 1)))):
                other[h] += q
        rows = []
        for b in range(0, span, int(step)):
            hs = range(b, min(span, b + int(step)))
            peak = max(hs, key=lambda h: mine[h] + other[h])
            cap = min(capacity(r, h) for h in hs)
            rows.append({"from": b, "schedule": mine[peak], "other": other[peak], "capacity": None if cap == float("inf") else cap})
        out[r] = rows
    return out


# ---- the plan's schedules ---------------------------------------------------------------------------------------

def _resolve(conn, refs) -> list[str]:
    from .exceptions import match

    ids, unmatched = match(conn, refs)
    for ref in unmatched:  # a tag or area by the name people write (a tag number, an area code)
        ids += [r["fuzer_id"] for r in conn.execute(
            "select l.fuzer_id from entity_label l join entity e using (fuzer_id) where l.label = %s "
            "and e.retired_at is null and e.merged_into is null order by e.kind = 'tag' desc limit 1", (ref,))
            if r["fuzer_id"] not in ids]
    return ids


def _order_on(conn, revision: str, tag_ids: list[str]) -> tuple[str, str] | None:
    """The revision's SAP order on a tag: (fuzer id, order number), or None when SAP hasn't loaded it."""
    if not tag_ids:
        return None
    row = conn.execute(
        """
        select e.fuzer_id, (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'work_order') as number
        from entity e join golden g on g.fuzer_id = e.fuzer_id and g.field = 'revision' and g.value #>> '{}' = %s
        join edge c on c.src = e.fuzer_id and c.rel = 'concerns' and c.dst = any(%s)
        where e.kind = 'work_order' and e.retired_at is null
        order by e.fuzer_id limit 1
        """, (revision, tag_ids)).fetchone()
    return (row["fuzer_id"], row["number"]) if row else None


def _parse_after(entry: str) -> tuple[str, str, float]:
    """'A0030' (finish-to-start), 'A0050 SS+12', 'A0110 FF-4' -> (code, type, lag hours)."""
    parts = str(entry).split()
    code, kind, lag = parts[0], "FS", 0.0
    if len(parts) > 1:
        rel = parts[1].upper()
        kind = rel[:2]
        if kind not in TYPES:
            raise PlanError(f"link {entry!r}: the type must be one of {', '.join(TYPES)}")
        if rel[2:]:
            try:
                lag = float(rel[2:])
            except ValueError as e:
                raise PlanError(f"link {entry!r}: the lag must be a number of hours") from e
    return code, kind, lag


P6 = "p6"  # who keeps a schedule that a planning system's export put on the plan


def import_template(conn, doc: dict, by: str, replace: bool = False) -> dict:
    """Put a schedule template on the plan: the project, its resources, an activity per step (matched to the assets
    it names, and to the revision's SAP order on its tag), the links and the resource needs, then schedule it.
    A project already on the plan is left alone unless `replace`."""
    from ..sources.relative_dates import offset, resolve

    doc = {**doc, "start": resolve(doc.get("start")), "finish_by": resolve(doc.get("finish_by"))}  # "today+21d 06:00"
    code = str(doc.get("code") or "").strip()
    if not code:
        raise PlanError("a schedule needs a code")
    if have := conn.execute("select created_by from plan_project where code = %s", (code,)).fetchone():
        # one the planning system's export put there gives way: the template is the planners' own schedule, with
        # the logic and resource needs the export doesn't carry, and the export's progress still follows it
        if not replace and have["created_by"] != P6:
            return {"project": code, "status": "kept"}
        remove_project(conn, code, by)
    kind = doc.get("kind") or "turnaround"
    if kind not in KINDS:
        raise PlanError(f"kind must be one of {KINDS}")
    roll = timedelta(days=offset())  # a rolling demo's template dates move on with its exports (#1085)
    start = plan._when(doc["start"]) + roll
    finish_by = plan._when(doc["finish_by"]) + roll if doc.get("finish_by") else None
    cal = doc.get("calendar") or {}
    conn.execute(
        "insert into plan_project (code, title, kind, start_at, finish_by, hours_per_day, days_per_week, notes, "
        "created_by, updated_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (code, doc.get("title") or code, kind, start, finish_by, cal.get("hours_per_day", 24),
         cal.get("days_per_week", 7), doc.get("notes"), by, by))
    for r in doc.get("resources") or []:
        set_resource(conn, r["code"], by, name=r.get("name"), kind=r.get("kind", "crew"), unit=r.get("unit"),
                     capacity=r.get("capacity"), work_center=r.get("work_center"), keep=True,
                     counts=r.get("counts"), routine=r.get("routine"))
    calendar = Calendar(start, cal.get("hours_per_day", 24), cal.get("days_per_week", 7))
    ids, summaries = {}, {}
    for a in doc.get("activities") or []:
        subjects = _resolve(conn, a.get("refs") or [])
        parent = None
        if a.get("order_of"):
            tag_ids = _resolve(conn, [a["order_of"]])
            order = _order_on(conn, code, tag_ids)
            if order:
                subjects = [order[0], *[s for s in subjects if s != order[0]]]
                parent = summaries.get(order[1]) or _summary(conn, code, kind, order, start, by)
                summaries[order[1]] = parent
        hours = float(a.get("hours") or 0)
        row = conn.execute(
            """
            insert into plan_item (kind, title, source, source_key, subjects, area_id, start_at, end_at, status, project,
                                   code, wbs, parent_id, duration_hours, needs_shutdown, notes, adds_boed, created_by,
                                   updated_by)
            values (%(kind)s, %(title)s, 'schedule', %(key)s, %(subjects)s, %(area)s, %(start)s, %(end)s, 'proposed',
                    %(project)s, %(code)s, %(wbs)s, %(parent)s, %(hours)s, %(shutdown)s, %(notes)s, %(adds)s, %(by)s, %(by)s)
            returning id
            """, {"kind": kind, "title": a["name"], "key": f"{code}:{a['code']}", "subjects": subjects,
                  "area": plan._area(conn, subjects), "start": start, "end": calendar.at(hours), "project": code,
                  "code": a["code"], "wbs": a.get("wbs"), "parent": parent, "hours": hours,
                  "shutdown": bool(a.get("shutdown", kind == "turnaround")), "notes": a.get("notes"),
                  "adds": a.get("adds_boed"), "by": by}).fetchone()
        ids[a["code"]] = row["id"]
        for r, q in (a.get("needs") or {}).items():
            set_demand(conn, row["id"], r, q, by)
    for a in doc.get("activities") or []:
        for entry in a.get("after") or []:
            pred, kind_, lag = _parse_after(entry)
            if pred not in ids:
                raise PlanError(f"{a['code']} follows {pred}, which isn't in the schedule")
            link(conn, ids[pred], ids[a["code"]], by, kind_, lag)
    applied = apply(conn, code, by)
    events.publish(conn, "plan.changed", f"plan_project:{code}", {"project": code, "change": "imported", "by": by,
                                                                  "activities": len(ids)})
    return {"project": code, "status": "imported", "activities": len(ids), "orders": len(summaries),
            "finish": applied["finish"]}


def _summary(conn, project: str, kind: str, order: tuple[str, str], start: datetime, by: str) -> int:
    """The SAP order's own plan item, as the summary its operations roll up into: the same row the SAP sync keeps
    (source sap, the order number), held to the schedule's dates."""
    fid, number = order
    row = conn.execute("select id from plan_item where source = 'sap' and source_key = %s", (number,)).fetchone()
    if row is None:
        row = plan.propose(conn, kind, f"Work order {number}", by, start, start, refs=[fid], source="sap",
                           source_key=number, needs_shutdown=True, notes=f"SAP order {number}, revision {project}")
    conn.execute("update plan_item set project = %s, moved_by = %s where id = %s", (project, by, row["id"]))
    conn.execute("delete from plan_demand where item_id = %s", (row["id"],))  # its operations carry the need now
    return row["id"]


def remove_project(conn, code: str, by: str) -> dict:
    """Take a schedule off the plan: its activities (SAP orders it summarised stay, as the SAP sync's)."""
    n = conn.execute("delete from plan_item where project = %s and source = 'schedule'", (code,)).rowcount
    conn.execute("update plan_item set project = null, moved_by = null where project = %s", (code,))
    conn.execute("delete from plan_project where code = %s", (code,))
    events.publish(conn, "plan.changed", f"plan_project:{code}", {"project": code, "change": "removed", "by": by})
    return {"project": code, "removed": n}


def seed(conn, project: str, by: str = "system") -> list[dict]:
    """The project's bundle schedule templates (bundle.yaml `schedules:`) not yet on its plan."""
    from ..core import projects as registry

    cfg = registry.config(conn, project)
    return [import_template(conn, doc, by) for doc in (cfg.get("bundle") or {}).get("schedules") or []]


def sync_activities(conn, by: str = P6) -> dict:
    """A planning system's schedules (Primavera P6, MS Project: `activity` entities from a connected source) as
    schedules on the integrated plan. Each top activity with activities under it is a schedule; each lowest activity
    under it an activity at the export's dates (held to start no earlier), with its percent complete, the assets it
    is located at and, where it executes an SAP order, that order as its group. The export carries no logic and no
    resource needs, so none are made up: a planner adds them on Plan, and the SAP orders bring their own crews.
    A schedule planners already keep on Plan under the same code is left alone, and so are the dates of an activity
    a person has moved; progress always follows the export. Activities the export dropped go."""
    from datetime import UTC, time

    from ..graph.operations import DONE, _day, _num

    rows = conn.execute(
        """
        select e.fuzer_id as id, min(l.label) as label,
               (select jsonb_object_agg(g.field, g.value) from golden g where g.fuzer_id = e.fuzer_id) as f,
               (select min(x.dst) from edge x join entity pe on pe.fuzer_id = x.dst and pe.kind = 'activity'
                where x.src = e.fuzer_id and x.rel = 'part_of') as parent,
               (select array_agg(distinct x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'located_at') as places,
               (select min(k.key) from edge x join entity_key k on k.fuzer_id = x.dst and k.kind = 'work_order'
                where x.src = e.fuzer_id and x.rel = 'executes') as work_order
        from entity e join entity_label l using (fuzer_id)
        where e.kind = 'activity' and e.retired_at is null and e.merged_into is null
        group by e.fuzer_id
        """).fetchall()
    by_id = {r["id"]: r for r in rows}
    parents = {r["parent"] for r in rows if r["parent"]}

    def root(r):
        seen = set()
        while r["parent"] in by_id and r["id"] not in seen:
            seen.add(r["id"])
            r = by_id[r["parent"]]
        return r

    under = defaultdict(list)
    for r in rows:
        top = root(r)
        if top["id"] != r["id"] and r["id"] not in parents:
            under[top["id"]].append(r)
    at = lambda d: datetime.combine(d, time(), UTC)
    out = {"schedules": 0, "activities": 0, "kept": [], "undated": 0, "removed": 0}
    for top_id, leaves in sorted(under.items(), key=lambda x: by_id[x[0]]["label"]):
        top = by_id[top_id]
        code, tf = top["label"], top["f"] or {}
        have = conn.execute("select created_by from plan_project where code = %s", (code,)).fetchone()
        if have and have["created_by"] != by:
            out["kept"].append(code)  # planners keep this one on Plan
            continue
        dated = []
        for r in leaves:
            f = r["f"] or {}
            s, e = _day(f.get("start")), _day(f.get("finish"))
            if s and e and e >= s:
                dated.append((r, f, at(s), at(e) + timedelta(days=1)))  # the finish day is worked
            else:
                out["undated"] += 1
        if not dated:
            continue
        start = min(d[2] for d in dated)
        finish_by = at(_day(tf["finish"])) + timedelta(days=1) if _day(tf.get("finish")) else max(d[3] for d in dated)
        conn.execute(
            """
            insert into plan_project (code, title, kind, start_at, finish_by, notes, created_by, updated_by)
            values (%s, %s, 'project', %s, %s, %s, %s, %s)
            on conflict (code) do update set title = excluded.title, start_at = excluded.start_at,
                finish_by = excluded.finish_by, updated_at = now()
            """, (code, str(tf.get("description") or tf.get("name") or code), start, max(finish_by, start + timedelta(days=1)),
                  "From the planning system's export: dates and progress follow it", by, by))
        keys = []
        for r, f, s, e in dated:
            pct = min(100.0, max(0.0, _num(f.get("percent_complete"))))
            done = pct >= 100 or str(f.get("status") or "").lower() in DONE or bool(f.get("actual_finish"))
            status = "done" if done else "in_progress" if pct > 0 or f.get("actual_start") else "scheduled"
            act = r["label"].removeprefix(f"{code}-") or r["label"]
            order = conn.execute("select id from plan_item where source = 'sap' and source_key = %s",
                                 (r["work_order"],)).fetchone() if r["work_order"] else None
            subjects = list(r["places"] or [])
            keys.append(f"{code}:{act}")
            conn.execute(
                """
                insert into plan_item (kind, title, source, source_key, subjects, area_id, start_at, end_at, status, project,
                                       code, wbs, parent_id, duration_hours, percent_complete, not_before, notes,
                                       created_by, updated_by)
                values ('project', %(title)s, 'schedule', %(key)s, %(subjects)s, %(area)s, %(s)s, %(e)s, %(status)s, %(project)s,
                        %(code)s, %(wbs)s, %(parent)s, %(hours)s, %(pct)s, %(s)s, %(notes)s, %(by)s, %(by)s)
                on conflict (source, source_key) where source_key is not null do update set
                    title = excluded.title, subjects = excluded.subjects, area_id = excluded.area_id, wbs = excluded.wbs,
                    parent_id = excluded.parent_id, percent_complete = excluded.percent_complete, status = excluded.status,
                    start_at = case when plan_item.updated_by = %(by)s then excluded.start_at else plan_item.start_at end,
                    end_at = case when plan_item.updated_by = %(by)s then excluded.end_at else plan_item.end_at end,
                    duration_hours = case when plan_item.updated_by = %(by)s then excluded.duration_hours
                                          else plan_item.duration_hours end,
                    not_before = case when plan_item.updated_by = %(by)s then excluded.not_before else plan_item.not_before end,
                    updated_at = now()
                """, {"title": str(f.get("description") or f.get("name") or r["label"]), "key": keys[-1], "subjects": subjects,
                      "area": plan._area(conn, subjects), "s": s, "e": e, "status": status, "project": code, "code": act,
                      "wbs": f.get("wbs"), "parent": order["id"] if order else None,
                      "hours": (e - s).total_seconds() / 3600, "pct": pct, "notes": f"Planning system activity {r['label']}",
                      "by": by})
        out["removed"] += conn.execute(
            "delete from plan_item where project = %s and source = 'schedule' and created_by = %s and not (source_key = any(%s))",
            (code, by, keys)).rowcount
        out["schedules"] += 1
        out["activities"] += len(keys)
    if out["schedules"]:
        events.publish(conn, "plan.changed", "plan_project:planning-system", {"change": "synced", "by": by, **out})
    return out


def projects(conn) -> list[dict]:
    return conn.execute(
        """
        select p.*, count(i.id) filter (where i.source = 'schedule') as activities,
               min(i.start_at) as first_start, max(i.end_at) filter (where i.source = 'schedule') as last_finish
        from plan_project p left join plan_item i on i.project = p.code
        group by p.code order by p.start_at
        """).fetchall()


def _project(conn, code: str | None) -> dict:
    row = (conn.execute("select * from plan_project where code = %s", (code,)).fetchone() if code else
           conn.execute("select * from plan_project where coalesce(finish_by, start_at) >= now() order by start_at limit 1")
           .fetchone() or conn.execute("select * from plan_project order by start_at desc limit 1").fetchone())
    if row is None:
        raise PlanError(f"no schedule {code}" if code else "no schedule is on the plan yet")
    return row


def _capacity_fn(conn, codes: set[str], calendar: Calendar):
    base = {r["code"]: r for r in conn.execute("select * from plan_resource where code = any(%s)", (list(codes),))}
    windows = defaultdict(list)
    for w in conn.execute("select * from plan_resource_capacity where resource = any(%s)", (list(codes),)):
        windows[w["resource"]].append((calendar.hours(w["start_at"]), calendar.hours(w["end_at"]), w["capacity"]))

    def capacity(r: str, h: int) -> float:
        for a, b, c in windows.get(r, ()):
            if a <= h < b:
                return c
        return base[r]["capacity"] if r in base else float("inf")
    return capacity, base


def analyse(conn, code: str | None = None) -> dict:
    """The schedule's critical path and its levelled schedule. Per activity: duration, early and late start and
    finish, total float, whether it is critical, the levelled start and finish and, when a resource held it back,
    which and by what. Then the unconstrained and levelled finish against the window, the critical chain, the
    resource overloads if everything started as early as its logic allows, and the other planned work counted against
    the same resources. Hours are working hours on the project's calendar."""
    p = _project(conn, code)
    cal = Calendar(p["start_at"], p["hours_per_day"], p["days_per_week"])
    acts = conn.execute(
        """
        select i.*, la.label as area_label,
               coalesce((select json_agg(l.label order by n) from unnest(i.subjects) with ordinality u (s, n)
                         join entity_label l on l.fuzer_id = s), '[]') as asset_labels,
               (select o.source_key from plan_item o where o.id = i.parent_id) as work_order,
               (select o.title from plan_item o where o.id = i.parent_id) as group_title
        from plan_item i left join entity_label la on la.fuzer_id = i.area_id
        where i.project = %s and i.source = 'schedule' and i.status <> 'cancelled'
        order by i.wbs nulls last, i.code
        """, (p["code"],)).fetchall()
    if not acts:
        raise PlanError(f"schedule {p['code']} has no activities")
    by_id = {a["id"]: a for a in acts}
    dur = {a["id"]: (0.0 if a["status"] == "done" else float(a["duration_hours"] or 0) * (1 - (a["percent_complete"] or 0) / 100))
           for a in acts}
    links = [(r["pred"], r["succ"], r["type"], r["lag_hours"]) for r in conn.execute(
        "select * from plan_link where pred = any(%(ids)s) and succ = any(%(ids)s)", {"ids": list(by_id)})]
    demands = defaultdict(dict)
    for r in conn.execute("select * from plan_demand_all where item_id = any(%s)", (list(by_id),)):
        demands[r["item_id"]][r["resource"]] = r["quantity"]
    used = {r for d in demands.values() for r in d}
    finish_by = cal.hours(p["finish_by"]) if p["finish_by"] else None
    not_before = {a["id"]: max(0.0, cal.hours(a["start_at"])) for a in acts if a["status"] in ("in_progress", "done")}
    for a in acts:  # a planner's "start no earlier than"
        if a["not_before"] and a["status"] not in ("in_progress", "done"):
            not_before[a["id"]] = max(not_before.get(a["id"], 0.0), cal.hours(a["not_before"]))
    cp = critical_path(dur, links, not_before)
    # Other planned work on the same resources in the window (an SAP order on the same work center, a campaign):
    # it holds capacity the schedule can't use, at its own dates.
    horizon_end = cal.at(max(cp["finish"] * 3, cp["finish"] + 24 * 30))
    other = conn.execute(
        """
        select i.id, i.title, i.start_at, i.end_at, d.resource, d.quantity, i.project, i.source, i.source_key, i.status
        from plan_item i left join plan_demand_all d on d.item_id = i.id
        where i.status = any(%s) and not (i.id = any(%s))
          and i.start_at < %s and i.end_at > %s and coalesce(i.project, '') <> %s
        order by i.start_at, i.id
        """, (list(plan.ACTIVE), list(by_id), horizon_end, p["start_at"], p["code"])).fetchall()
    used |= {o["resource"] for o in other if o["resource"] and o["start_at"] < cal.at(cp["finish"])}  # the crews that other work holds are shown beside it
    capacity, resources = _capacity_fn(conn, used, cal)
    background = defaultdict(list)
    for o in other:
        if o["resource"] in used:
            background[o["resource"]].append((cal.hours(o["start_at"]), cal.hours(o["end_at"]), o["quantity"]))
    routine = routine_load(conn, resources.values())
    for r, x in routine.items():  # rounds and checks hold their people the whole way through
        background[r].append((0.0, cal.hours(horizon_end), x["quantity"]))
    lv = level(dur, links, demands, capacity, background, not_before)
    early = overloads(dur, cp["es"], demands, capacity, background)
    late = overloads(dur, lv["start"], demands, capacity, background)
    label = lambda i: by_id[i]["code"] or str(i)
    span = max(lv["end"], cp["finish"])
    step = p["hours_per_day"]

    def histogram(starts):
        return {r: [{"day": cal.at(x["from"]).date().isoformat(), **{k: x[k] for k in ("schedule", "other", "capacity")}}
                    for x in rows] for r, rows in profile(dur, starts, demands, capacity, background, span, step).items()}
    rows = []
    for a in acts:
        i = a["id"]
        rows.append({"id": i, "code": a["code"], "wbs": a["wbs"], "name": a["title"], "status": a["status"],
                     "duration_hours": a["duration_hours"], "percent_complete": a["percent_complete"],
                     "work_order": a["work_order"], "group": a["group_title"], "assets": a["asset_labels"], "area": a["area_label"],
                     "needs": demands.get(i, {}),
                     "predecessors": [{"code": label(pp), "type": t, "lag_hours": lag} for pp, s, t, lag in links if s == i],
                     "early_start": cal.at(cp["es"][i]), "early_finish": cal.at(cp["ef"][i]),
                     "late_start": cal.at(cp["ls"][i]), "late_finish": cal.at(cp["lf"][i]),
                     "float_hours": round(cp["float"][i], 1), "critical": i in cp["critical"],
                     # finished work is shown where it was done; the rest where the logic and the crews put it
                     "start": a["start_at"] if a["status"] == "done" else cal.at(lv["start"][i]),
                     "finish": a["end_at"] if a["status"] == "done" else cal.at(lv["finish"][i]),
                     "held_back_hours": round(lv["waited"][i], 1),
                     "held_back_by": ({"resource": lv["why"][i]["resource"],
                                       "activities": [label(x) for x in lv["why"][i]["held_by"]]} if i in lv["why"] else None),
                     "planned_start": a["start_at"], "planned_finish": a["end_at"], "not_before": a["not_before"],
                     "adds_boed": a["adds_boed"]})
    finish, levelled = cal.at(cp["finish"]), cal.at(lv["end"])

    def over(rows):
        return [{"resource": o["resource"], "name": resources.get(o["resource"], {}).get("name"),
                 "from": cal.at(o["from"]), "to": cal.at(o["to"]), "need": o["peak"], "capacity": o["capacity"],
                 "unit": resources.get(o["resource"], {}).get("unit"), "activities": [label(x) for x in o["activities"]],
                 "other_work": o["other_work"]} for o in rows]
    return {
        "project": {k: p[k] for k in ("code", "title", "kind", "start_at", "finish_by", "hours_per_day", "days_per_week")},
        "source": "the schedule on Fuzer's integrated plan (activities, links and resource needs planners maintain)",
        "activities": rows,
        "critical_path": [label(i) for i in cp["chain"]],
        "unconstrained_finish": finish,
        "levelled_finish": levelled,
        "levelled_days_longer": round((lv["end"] - cp["finish"]) / p["hours_per_day"], 1),
        "overruns_window_by_days": (round((levelled - p["finish_by"]).total_seconds() / 86400, 1)
                                    if p["finish_by"] and levelled > p["finish_by"] else 0),
        "levelled_chain": [{"code": label(c["activity"]), **({"waited_for": c["waited_for"]} if "waited_for" in c else {})}
                           for c in lv["chain"]],
        "overloads_if_unlevelled": over(early),
        "overloads_levelled": over(late),  # left only where capacity is below one activity's own need
        "resources": [{"code": r["code"], "name": r["name"], "kind": r["kind"], "unit": r["unit"],
                       "capacity": r["capacity"], "counts": r["counts"], "routine": routine.get(r["code"])}
                      for r in resources.values()],
        "profiles": {"levelled": histogram(lv["start"]), "unlevelled": histogram(cp["es"])},
        # every other active job in the schedule's own span: SAP orders, campaigns, other schedules' work; a row per
        # resource it needs (resource null when it needs none)
        "other_work": [{"id": o["id"], "title": o["title"], "resource": o["resource"], "quantity": o["quantity"],
                        "start": o["start_at"], "end": o["end_at"], "project": o["project"],
                        "order": o["source_key"] if o["source"] == "sap" else None, "status": o["status"]}
                       for o in other if o["start_at"] < max(levelled, finish) and (o["resource"] is None or o["resource"] in used)],
        "hours": "working hours on the project's calendar",
        "window_hours": finish_by,
    }


def apply(conn, code: str, by: str) -> dict:
    """Schedule: write the levelled dates onto the activities, and each SAP order summary's span from its operations,
    so the integrated plan shows the schedule as it would run."""
    out = analyse(conn, code)
    for a in out["activities"]:
        conn.execute("update plan_item set start_at = %s, end_at = %s, updated_by = %s, updated_at = now() "
                     "where id = %s and (start_at, end_at) is distinct from (%s, %s)",
                     (a["start"], a["finish"], by, a["id"], a["start"], a["finish"]))
    conn.execute(
        """
        update plan_item o set start_at = s.first, end_at = s.last, moved_by = %(by)s, updated_by = %(by)s, updated_at = now()
        from (select parent_id, min(start_at) as first, max(end_at) as last from plan_item
              where project = %(code)s and source = 'schedule' and parent_id is not null group by parent_id) s
        where o.id = s.parent_id
        """, {"by": by, "code": out["project"]["code"]})
    events.publish(conn, "plan.changed", f"plan_project:{out['project']['code']}",
                   {"project": out["project"]["code"], "change": "scheduled", "by": by,
                    "finish": out["levelled_finish"].isoformat()})
    return {"project": out["project"]["code"], "finish": out["levelled_finish"],
            "unconstrained_finish": out["unconstrained_finish"]}


# ---- what a planner edits ---------------------------------------------------------------------------------------

def _activity(conn, item_id: int) -> dict:
    row = conn.execute("select * from plan_item where id = %s and project is not null and source = 'schedule'",
                       (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no schedule activity {item_id}")
    return row


def add_activity(conn, project: str, code: str, name: str, hours: float, by: str, wbs: str | None = None,
                 refs=(), after=()) -> dict:
    p = _project(conn, project)
    if hours is None or hours < 0:
        raise PlanError("an activity's duration is a number of working hours, 0 or more")
    if conn.execute("select 1 from plan_item where project = %s and code = %s", (p["code"], code)).fetchone():
        raise PlanError(f"{p['code']} already has an activity {code}")
    subjects = _resolve(conn, refs)
    cal = Calendar(p["start_at"], p["hours_per_day"], p["days_per_week"])
    row = conn.execute(
        """
        insert into plan_item (kind, title, source, source_key, subjects, area_id, start_at, end_at, project, code, wbs,
                               duration_hours, needs_shutdown, created_by, updated_by)
        values (%s, %s, 'schedule', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (p["kind"], name.strip(), f"{p['code']}:{code}", subjects, plan._area(conn, subjects), p["start_at"],
              cal.at(hours), p["code"], code, wbs, hours, p["kind"] == "turnaround", by, by)).fetchone()
    for entry in after or ():
        pred, kind, lag = _parse_after(entry)
        other = conn.execute("select id from plan_item where project = %s and code = %s", (p["code"], pred)).fetchone()
        if other is None:
            raise PlanError(f"{p['code']} has no activity {pred}")
        link(conn, other["id"], row["id"], by, kind, lag)
    return row


def change_activity(conn, item_id: int, by: str, **fields) -> dict:
    """Rename, re-time (duration in working hours), progress (percent complete, status), re-number an activity, or
    hold it to start no earlier than a time (`not_before`; None lets it start as early as its logic allows), or say
    how much production it brings online when it finishes (`adds_boed`, BOE/d: production capacity counts it)."""
    _activity(conn, item_id)
    allowed = {"name": "title", "duration_hours": "duration_hours", "percent_complete": "percent_complete",
               "wbs": "wbs", "code": "code", "status": "status", "notes": "notes", "not_before": "not_before",
               "adds_boed": "adds_boed"}
    bad = set(fields) - set(allowed)
    if bad:
        raise PlanError(f"can't change {sorted(bad)}")
    if "duration_hours" in fields and (fields["duration_hours"] is None or fields["duration_hours"] < 0):
        raise PlanError("an activity's duration is a number of working hours, 0 or more")
    if "percent_complete" in fields and not 0 <= (fields["percent_complete"] or 0) <= 100:
        raise PlanError("percent complete is between 0 and 100")
    if "status" in fields and fields["status"] not in STATUSES:
        raise PlanError(f"status must be one of {STATUSES}")
    if fields.get("adds_boed") is not None and fields["adds_boed"] < 0:
        raise PlanError("the production an activity brings online is a number of BOE/d, 0 or more")
    sets = {allowed[k]: v for k, v in fields.items()}
    if not sets:
        return _activity(conn, item_id)
    return conn.execute("update plan_item set " + ", ".join(f"{k} = %({k})s" for k in sets) +
                        ", updated_by = %(by)s, updated_at = now() where id = %(id)s returning *",
                        {**sets, "by": by, "id": item_id}).fetchone()


def remove_activity(conn, item_id: int, by: str) -> dict:
    a = _activity(conn, item_id)
    conn.execute("delete from plan_item where id = %s", (item_id,))
    events.publish(conn, "plan.changed", f"plan_item:{item_id}", {"id": item_id, "change": "removed", "by": by,
                                                                  "title": a["title"]})
    return {"removed": item_id, "code": a["code"]}


def link(conn, pred: int, succ: int, by: str, kind: str = "FS", lag_hours: float = 0) -> dict:
    """The successor waits for the predecessor (FS, SS, FF or SF, with a lag in working hours). A link that would
    close a loop is refused."""
    if kind not in TYPES:
        raise PlanError(f"a link's type is one of {', '.join(TYPES)}")
    a, b = _activity(conn, pred), _activity(conn, succ)
    if a["project"] != b["project"]:
        raise PlanError("both activities must be in the same schedule")
    if pred == succ:
        raise PlanError("an activity can't follow itself")
    looped = conn.execute(
        """
        with recursive down (id) as (select %(succ)s::bigint union select l.succ from plan_link l join down on l.pred = down.id)
        select 1 from down where id = %(pred)s
        """, {"pred": pred, "succ": succ}).fetchone()
    if looped:
        raise PlanError(f"{b['code']} already leads to {a['code']}: this link would loop")
    return conn.execute(
        "insert into plan_link (pred, succ, type, lag_hours, created_by) values (%s, %s, %s, %s, %s) "
        "on conflict (pred, succ) do update set type = excluded.type, lag_hours = excluded.lag_hours returning *",
        (pred, succ, kind, float(lag_hours or 0), by)).fetchone()


def unlink(conn, pred: int, succ: int, by: str) -> dict:
    n = conn.execute("delete from plan_link where pred = %s and succ = %s", (pred, succ)).rowcount
    return {"removed": n}


def set_demand(conn, item_id: int, resource: str, quantity: float | None, by: str) -> dict:
    """What an activity needs of a resource while it runs (none removes it)."""
    if not conn.execute("select 1 from plan_resource where code = %s", (resource,)).fetchone():
        raise PlanError(f"no resource {resource}")
    if not quantity:
        conn.execute("delete from plan_demand where item_id = %s and resource = %s", (item_id, resource))
        return {"item_id": item_id, "resource": resource, "quantity": None}
    return conn.execute(
        "insert into plan_demand (item_id, resource, quantity) values (%s, %s, %s) "
        "on conflict (item_id, resource) do update set quantity = excluded.quantity returning *",
        (item_id, resource, float(quantity))).fetchone()


def set_resource(conn, code: str, by: str, name: str | None = None, kind: str | None = None, unit: str | None = None,
                 capacity: float | None = None, work_center: str | None = None, keep: bool = False,
                 counts: list[str] | None = None, routine: str | None = None) -> dict:
    """Add a shared resource or change what it has. `keep`: an existing one is left as a planner set it."""
    row = conn.execute("select * from plan_resource where code = %s", (code,)).fetchone()
    if row and keep:
        return row
    kind = kind or (row["kind"] if row else "crew")
    if kind not in RESOURCE_KINDS:
        raise PlanError(f"a resource's kind is one of {', '.join(RESOURCE_KINDS)}")
    cap = capacity if capacity is not None else (row["capacity"] if row else None)
    if cap is None or cap < 0:
        raise PlanError("a resource needs a capacity of 0 or more")
    # counts: the resources whose people it also carries; routine: "rounds", or "" for none
    counts = [c.strip() for c in counts if c.strip()] if counts is not None else (row["counts"] if row else [])
    known = {r["code"]: r for r in conn.execute("select code, counts from plan_resource where code = any(%s)", (counts,))}
    for c in counts:
        if c == code or c not in known or known[c]["counts"]:
            raise PlanError(f"{code} can count the people on another resource that counts none itself; {c} isn't one")
    routine = (routine or None) if routine is not None else (row["routine"] if row else None)
    if routine and routine not in ROUTINES:
        raise PlanError(f"routine duties are one of {', '.join(ROUTINES)}")
    return conn.execute(
        """
        insert into plan_resource (code, name, kind, unit, capacity, work_center, counts, routine, created_by, updated_by)
        values (%(code)s, %(name)s, %(kind)s, %(unit)s, %(cap)s, %(wc)s, %(counts)s, %(routine)s, %(by)s, %(by)s)
        on conflict (code) do update set name = excluded.name, kind = excluded.kind, unit = excluded.unit,
            capacity = excluded.capacity, work_center = excluded.work_center, counts = excluded.counts,
            routine = excluded.routine, updated_by = excluded.updated_by,
            updated_at = now()
        returning *
        """, {"code": code, "name": name or (row["name"] if row else code), "kind": kind,
              "unit": unit or (row["unit"] if row else "people"), "cap": cap,
              "wc": work_center if work_center is not None else (row["work_center"] if row else None),
              "counts": counts, "routine": routine, "by": by}).fetchone()



def _crew_changes(conn, res, start: datetime, end: datetime) -> dict[str, list[dict]]:
    """Seats' demand, worked out from the schedules: each crew a seats resource counts flies out at its peak size
    the day its first job on a schedule starts and back the day its last job finishes. Per seats resource, one row
    per crew, schedule and direction, lasting that day."""
    seats = [r for r in res if r["kind"] == "seats" and r["counts"]]
    if not seats:
        return {}
    moves = _crew_moves(conn, sorted({c for r in seats for c in r["counts"]}), start, end)
    return {r["code"]: [m | {"resource": r["code"]} for m in moves if m["crew"] in r["counts"]] for r in seats}


def _crew_trips(conn, crews: list[str]) -> list[dict]:
    """Each of `crews` on each schedule it works: its jobs there, the most of it working at once (how many travel and
    take a bed), and its first start and last finish (out the day of the one, back the day of the other)."""
    names = {r["code"]: r["name"] for r in conn.execute("select code, name from plan_resource where code = any(%s)", (crews,))}
    jobs = conn.execute(
        """
        select d.resource, i.project, i.start_at, i.end_at, d.quantity
        from plan_demand d join plan_item i on i.id = d.item_id
        where d.resource = any(%s) and i.status = any(%s) and i.project is not null and i.source = 'schedule'
          and d.quantity > 0
        """, (crews, list(plan.ACTIVE))).fetchall()
    groups = defaultdict(list)
    for j in jobs:
        groups[(j["resource"], j["project"])].append(j)
    out = []
    for (crew, project), js in sorted(groups.items()):
        edges = sorted([(j["start_at"], 1, j["quantity"]) for j in js] + [(j["end_at"], 0, -j["quantity"]) for j in js])
        size = peak = 0.0
        for _, _, q in edges:
            size += q
            peak = max(peak, size)
        out.append({"crew": crew, "name": names.get(crew, crew), "project": project, "jobs": js, "peak": peak,
                    "first": min(j["start_at"] for j in js), "last": max(j["end_at"] for j in js)})
    return out


def _crew_moves(conn, crews: list[str], start: datetime, end: datetime) -> list[dict]:
    """Each of `crews` travelling for a schedule: out at its peak size the day its first job starts, back the day
    its last job finishes. One row per crew, schedule and direction, lasting that day."""
    moves = []
    for t in _crew_trips(conn, crews):
        for way, at in (("flies out", t["first"]), ("flies back", t["last"])):
            day = at.replace(hour=0, minute=0, second=0, microsecond=0)
            if day < end and day + timedelta(days=1) > start:
                moves.append({"crew": t["crew"], "id": None, "title": f"{t['name']} {way}", "kind": "crew_change",
                              "project": t["project"], "code": None, "source": "crew change", "source_key": None,
                              "start_at": day, "end_at": day + timedelta(days=1), "status": "scheduled",
                              "quantity": t["peak"], "way": "out" if way == "flies out" else "back"})
    return moves


def _crew_stays(conn, res, start: datetime, end: datetime) -> dict[str, list[dict]]:
    """Beds' demand between a visiting crew's jobs: a crew that flew out stays aboard, at the size it travelled, until
    it flies back after its last job, so every hour from its first start to its last finish when fewer of it are on
    jobs holds the rest of its beds (a day with no job on it holds them all). Per beds resource, one row per run of
    hours, with the shortfall as its quantity; the jobs' own hours are counted from the jobs as before."""
    beds = [r for r in res if r["kind"] == "beds" and r["counts"]]
    if not beds:
        return {}
    rows = []
    for t in _crew_trips(conn, sorted({c for r in beds for c in r["counts"]})):
        if t["last"] <= start or t["first"] >= end:
            continue
        cuts = sorted({t["first"], t["last"], *(j["start_at"] for j in t["jobs"]), *(j["end_at"] for j in t["jobs"])})
        for a, b in zip(cuts, cuts[1:]):
            working = sum(j["quantity"] for j in t["jobs"] if j["start_at"] < b and j["end_at"] > a)
            idle = t["peak"] - working
            if idle <= EPS:
                continue
            if rows and rows[-1]["crew"] == t["crew"] and rows[-1]["project"] == t["project"] \
                    and rows[-1]["end_at"] == a and abs(rows[-1]["quantity"] - idle) <= EPS:
                rows[-1]["end_at"] = b
                continue
            rows.append({"crew": t["crew"], "id": None, "title": f"{t['name']} aboard between jobs", "kind": "crew_stay",
                         "project": t["project"], "code": None, "source": "crew stay", "source_key": None,
                         "start_at": a, "end_at": b, "status": "scheduled", "quantity": idle})
    return {r["code"]: [x | {"resource": r["code"]} for x in rows if x["crew"] in r["counts"]] for r in beds}


PRODUCING = "Producing now"  # the source production capacity shows for what the wells make today


def _online(conn, res, start: datetime, end: datetime) -> dict:
    """Production capacity's demand, worked out from the plan: how much is online, in BOE/d, against how much the
    system can handle. `wells` is what every well on the flow network makes at its latest test (ops/at_risk.py's
    reads); `down` is each active job that needs a unit down, with the wells behind that unit, which are off while
    it runs (a well two overlapping jobs take down is off once); `adds` is each active job that brings production
    on when it finishes (`plan_item.adds_boed`), counted from its finish."""
    if not any(r["kind"] == "capacity" for r in res):
        return {"wells": {}, "down": [], "adds": []}
    from ..core.units import boe
    from . import at_risk

    feeds, nodes = at_risk._flow(conn)
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where fuzer_id = any(%s)", (list(nodes),))} if nodes else {}
    rates = at_risk._rates(conn, sorted(n for n in nodes if kinds.get(n) == "well"))
    made = {w: boe(sum(q[k]["value"] or 0 for k in ("oil_rate", "condensate_rate") if k in q),
                   (q.get("gas_rate") or {}).get("value")) for w, q in rates.items()}
    made = {w: v for w, v in made.items() if v}
    cols = "id, title, kind, project, code, source, source_key, start_at, end_at, status"
    down = []
    for r in conn.execute(
            f"""
            select {cols}, subjects from plan_item
            where status = any(%s) and (needs_shutdown or kind = any(%s)) and start_at < %s and end_at > %s
            order by start_at, id
            """, (list(plan.ACTIVE), list(plan.SHUTDOWNS), end, start)).fetchall():
        behind = at_risk._upstream(at_risk._units(conn, list(r["subjects"] or []), nodes), feeds)
        wells = {w: made[w] for w in sorted(behind) if w in made}
        if wells:
            down.append({**{k: r[k] for k in r if k != "subjects"}, "wells": wells, "quantity": round(sum(wells.values()), 1)})
    adds = [{**r, "arrives": r["end_at"], "quantity": float(r["adds_boed"])} for r in conn.execute(
        f"select {cols}, adds_boed from plan_item where status = any(%s) and adds_boed > 0 and end_at < %s "
        "order by end_at, id", (list(plan.ACTIVE), end)).fetchall()]
    return {"wells": made, "down": down, "adds": adds}


ROUND_CHECK_MINUTES = 4  # walking to a point, reading or checking it and keying it in
ROUTINES = ("rounds",)


def routine_load(conn, resources) -> dict[str, dict]:
    """The people each resource's routine duties hold, whatever else is planned. `rounds`: every round point is taken
    once a shift (ops/rounds.py), at ROUND_CHECK_MINUTES each, spread over the shift."""
    todo = [r for r in resources if r.get("routine") == "rounds"]
    if not todo:
        return {}
    from . import rounds
    from .handover import shift_times

    checks = len(rounds.points(conn))
    if not checks:
        return {}
    shift_hours = 24 / max(1, len(shift_times(conn)))
    people = round(checks * ROUND_CHECK_MINUTES / 60 / shift_hours, 2)
    return {r["code"]: {"quantity": people, "source": "Rounds and checks",
                        "basis": f"{checks} round checks a shift at {ROUND_CHECK_MINUTES} minutes each, over a {shift_hours:g}-hour shift"}
            for r in todo} if people > 0 else {}


# ---- a resource owner's view ------------------------------------------------------------------------------------

def _source(row) -> str:
    """Where a piece of demand comes from, as the owner reads it: a schedule's code, or the kind of planned work."""
    return row["project"] or {"maintenance": "SAP orders"}.get(row["kind"], row["kind"].replace("_", " "))


def resource_load(conn, resource: str | None = None, start: datetime | None = None, days: int = 42) -> dict:
    """What every source on the plan asks of each shared resource, day by day, against its capacity: per resource
    and day the peak hour's demand split by source (each schedule, SAP orders on the work center, campaigns, other
    work) and the least capacity that day, then each run of hours over capacity with the jobs in it and the move
    that clears it at the least cost (the job with the most float, or work with no schedule logic behind it,
    slipped to the end of the run). `resource` narrows it to one; times are wall-clock hours."""
    start = (start or clock.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:  # the page sends a day, and clock.now() is wall clock: both are the plant's time
        start = start.replace(tzinfo=clock.zone())
    end = start + timedelta(days=days)
    res = conn.execute("select * from plan_resource" + (" where code = %s" if resource else "") + " order by kind, code",
                       (resource,) if resource else ()).fetchall()
    if resource and not res:
        raise PlanError(f"no resource {resource}")
    codes = [r["code"] for r in res]
    rows = conn.execute(
        """
        select i.id, i.title, i.kind, i.project, i.code, i.source, i.source_key, i.start_at, i.end_at, i.status,
               d.resource, d.quantity
        from plan_demand_all d join plan_item i on i.id = d.item_id
        where d.resource = any(%s) and i.status = any(%s) and i.start_at < %s and i.end_at > %s and d.quantity > 0
        order by i.start_at, i.id
        """, (codes, list(plan.ACTIVE), end, start)).fetchall()
    windows = defaultdict(list)
    for w in conn.execute("select * from plan_resource_capacity where resource = any(%s) and start_at < %s and end_at > %s "
                          "order by start_at", (codes, end, start)):
        windows[w["resource"]].append(w)
    hours = days * 24
    h_of = lambda t: (t - start).total_seconds() / 3600
    # float per schedule activity, from each schedule's own analysis, so a move can say what it costs
    floats: dict[int, float] = {}
    for p in {r["project"] for r in rows if r["project"] and r["source"] == "schedule"}:
        try:
            for a in analyse(conn, p)["activities"]:
                floats[a["id"]] = a["float_hours"]
        except PlanError:
            pass
    routine = routine_load(conn, res)
    trips = _crew_changes(conn, res, start, end)
    stays = _crew_stays(conn, res, start, end)
    online = _online(conn, res, start, end)
    out = []
    for r in res:
        code = r["code"]
        cap = [float(r["capacity"])] * hours
        for w in windows[code]:
            for h in range(max(0, int(h_of(w["start_at"]))), min(hours, int(-(-h_of(w["end_at"]) // 1)))):
                cap[h] = float(w["capacity"])
        use: list[dict[str, float]] = [defaultdict(float) for _ in range(hours)]
        jobs: list[list] = [[] for _ in range(hours)]
        mine = [x for x in rows if x["resource"] == code] + trips.get(code, []) + stays.get(code, [])
        for x in mine:
            for h in range(max(0, int(h_of(x["start_at"]))), min(hours, int(-(-h_of(x["end_at"]) // 1)))):
                use[h][_source(x)] += x["quantity"]
                jobs[h].append(x)
        if r["kind"] == "capacity":  # what is online: the wells' rates, less what jobs have down, plus what jobs bring on
            down: list[set] = [set() for _ in range(hours)]
            for x in online["down"]:
                for h in range(max(0, int(h_of(x["start_at"]))), min(hours, int(-(-h_of(x["end_at"]) // 1)))):
                    down[h] |= set(x["wells"])
            field = sum(online["wells"].values())
            for h in range(hours):
                use[h][PRODUCING] += field - sum(online["wells"][w] for w in down[h])
            for x in online["adds"]:  # from its finish on; moving the job later moves the production later
                arrival = x | {"start_at": x["arrives"]}
                for h in range(max(0, int(-(-h_of(x["arrives"]) // 1))), hours):
                    use[h][_source(x)] += x["quantity"]
                    jobs[h].append(arrival)
        if code in routine:
            for u in use:
                u[routine[code]["source"]] += routine[code]["quantity"]
        total = [sum(u.values()) for u in use]
        sources = sorted({s for u in use for s in u})
        days_out = []
        for d in range(days):
            hs = range(d * 24, d * 24 + 24)
            peak = max(hs, key=lambda h: (total[h] - cap[h], total[h]))
            days_out.append({"day": (start + timedelta(days=d)).date().isoformat(), "capacity": min(cap[h] for h in hs),
                             "demand": round(total[peak], 2), "by_source": {s: round(v, 2) for s, v in use[peak].items()}})
        runs, h = [], 0
        while h < hours:
            if total[h] > cap[h] + EPS:
                a = h
                while h < hours and total[h] > cap[h] + EPS:
                    h += 1
                in_run = {x["id"] or (x["title"], x["project"]): x for hh in range(a, h) for x in jobs[hh]}
                over = max(total[hh] - cap[hh] for hh in range(a, h))
                moves = []
                for x in in_run.values():
                    if x["status"] == "in_progress" or x["quantity"] + EPS < over or x["id"] is None:
                        continue  # started work stays; moving a job too small to clear the run doesn't clear it
                    shift = h_of(start + timedelta(hours=h)) - h_of(x["start_at"])
                    fl = floats.get(x["id"])
                    late = max(0.0, shift - fl) if fl is not None else None
                    moves.append({"item_id": x["id"], "title": x["title"], "source": _source(x), "code": x["code"],
                                  "order": x["source_key"] if x["source"] != "schedule" else None,
                                  "slip_hours": round(shift, 1), "float_hours": fl,
                                  "finish_later_days": round(late / 24, 1) if late is not None else None,
                                  "new_start": start + timedelta(hours=h),
                                  "new_end": x["end_at"] + timedelta(hours=shift)})
                moves.sort(key=lambda m: (m["finish_later_days"] if m["finish_later_days"] is not None else 0.5,
                                          m["slip_hours"], m["item_id"]))
                runs.append({"from": start + timedelta(hours=a), "to": start + timedelta(hours=h),
                             "peak": round(max(total[a:h]), 2), "capacity": min(cap[a:h]), "over_by": round(over, 2),
                             "jobs": [{"item_id": x["id"], "title": x["title"], "source": _source(x), "code": x["code"],
                                       "quantity": x["quantity"]} for x in in_run.values()],
                             "moves": moves[:3]})
            else:
                h += 1
        out.append({"code": code, "name": r["name"], "kind": r["kind"], "unit": r["unit"], "capacity": r["capacity"],
                    "work_center": r["work_center"], "counts": r["counts"], "routine": r["routine"],
                    "routine_load": routine.get(code), "sources": sources, "days": days_out, "overloads": runs,
                    "capacity_windows": [{"id": w["id"], "start": w["start_at"], "end": w["end_at"],
                                          "capacity": w["capacity"], "reason": w["reason"]} for w in windows[code]]})
    return {"start": start, "end": end, "days": days, "resources": out,
            "basis": "every active planned job's resource needs, at its planned dates, against each resource's capacity"}


def set_capacity_window(conn, resource: str, start: datetime, end: datetime, capacity: float, by: str,
                        reason: str | None = None) -> dict:
    """A resource has a different capacity for a while (leave, a crane's certification, a vessel in dock)."""
    if not conn.execute("select 1 from plan_resource where code = %s", (resource,)).fetchone():
        raise PlanError(f"no resource {resource}")
    if end <= start or capacity < 0:
        raise PlanError("a capacity window needs an end after its start and a capacity of 0 or more")
    row = conn.execute("insert into plan_resource_capacity (resource, start_at, end_at, capacity, reason, created_by) "
                       "values (%s, %s, %s, %s, %s, %s) returning *", (resource, start, end, capacity, reason, by)).fetchone()
    events.publish(conn, "plan.changed", f"plan_resource:{resource}", {"resource": resource, "change": "capacity window",
                                                                      "by": by})
    return row


def remove_capacity_window(conn, window_id: int, by: str) -> dict:
    row = conn.execute("delete from plan_resource_capacity where id = %s returning resource", (window_id,)).fetchone()
    if not row:
        raise PlanError(f"no resource capacity window {window_id}")
    events.publish(conn, "plan.changed", f"plan_resource:{row['resource']}", {"resource": row["resource"],
                                                                              "change": "capacity window removed", "by": by})
    return {"removed": window_id}


# ---- logistics, from the schedule -------------------------------------------------------------------------------

EQUIPMENT = ("crane", "vessel", "deck")  # what is booked for a job, not staffed


def _materials(conn, start: datetime, end: datetime) -> dict[str, list[dict]]:
    """The parts each day's jobs need on site, by day: every open reservation on a work order a planned job carries
    out, due the day the first job on that order starts (a schedule's first activity on it, or the SAP order's own
    planned job), with whether stock or an order on record covers it (analysis/material_readiness). An order whose
    first job started before `start` has had its parts sent already, and is left out."""
    from ..analysis import material_readiness

    first: dict[str, dict] = {}
    for r in conn.execute(
            """
            select i.id, i.title, i.code, i.project, i.source, i.source_key, i.start_at, e.fuzer_id as work_order
            from plan_item i cross join lateral unnest(i.subjects) s (fuzer_id)
            join entity e on e.fuzer_id = s.fuzer_id and e.kind = 'work_order'
            where i.status = any(%s) and i.start_at < %s
            order by i.start_at, i.source <> 'schedule', i.id
            """, (list(plan.ACTIVE), end)):
        first.setdefault(r["work_order"], r)
    sending = {o: r for o, r in first.items() if r["start_at"] >= start}
    if not sending:
        return {}
    out: dict[str, list[dict]] = defaultdict(list)
    for line in material_readiness.coverage(conn)["lines"]:
        job = sending.get(line["work_order_id"])
        if not job:
            continue
        due = max((c["due"] for c in line["on_order"] if c["due"]), default=None)
        out[job["start_at"].astimezone(start.tzinfo).date().isoformat()].append({
            "material": line["material"], "material_id": line["material_id"], "description": line["description"],
            "quantity": line["quantity"], "order": line["work_order"], "item_id": job["id"], "title": job["title"],
            "code": job["code"], "schedule": job["project"], "verdict": line["verdict"], "due": due,
            "days_late": line.get("days_late"), "short": line.get("short")})
    rank = material_readiness.VERDICT_ORDER
    return {d: sorted(xs, key=lambda x: (rank.index(x["verdict"]), x["order"] or "", x["material"] or ""))
            for d, xs in out.items()}


def logistics(conn, start: datetime | None = None, days: int = 14) -> dict:
    """What the plan asks to be sent, day by day: each crew's jobs with the people and the assets, the crews that
    travel (those a seats or beds resource counts: out the day their first job on a schedule starts, back the day
    their last finishes) with the seats and beds they take against what there is (a crew aboard keeps its beds on a
    day between its jobs, listed under `aboard`), the cranes, vessels and deck space booked, and the parts to send
    for the jobs that start that day with whether they are in stock or on order. Everything is read from the plan's
    dates, resource needs and the work orders' reservations; nothing is committed here."""
    start = (start or clock.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=clock.zone())
    end = start + timedelta(days=days)
    res = {r["code"]: r for r in conn.execute("select * from plan_resource order by kind, code")}
    rows = conn.execute(
        """
        select i.id, i.title, i.project, i.code, i.source, i.source_key, i.start_at, i.end_at, i.status,
               d.resource, d.quantity, la.label as area,
               coalesce((select json_agg(l.label order by n) from unnest(i.subjects) with ordinality u (s, n)
                         join entity_label l on l.fuzer_id = s), '[]') as assets
        from plan_demand d join plan_item i on i.id = d.item_id left join entity_label la on la.fuzer_id = i.area_id
        where i.status = any(%s) and i.start_at < %s and i.end_at > %s and d.quantity > 0
        order by i.start_at, i.id
        """, (list(plan.ACTIVE), end, start)).fetchall()
    carriers = [r for r in res.values() if r["kind"] in ("seats", "beds") and r["counts"]]
    travelling = sorted({c for r in carriers for c in r["counts"]})
    moves = _crew_moves(conn, travelling, start, end) if travelling else []
    by_seats = {c: r for r in carriers if r["kind"] == "seats" for c in r["counts"]}
    bedded = sorted({c for r in carriers if r["kind"] == "beds" for c in r["counts"]})
    stays = [t for t in _crew_trips(conn, bedded) if t["last"] > start and t["first"] < end] if bedded else []
    materials = _materials(conn, start, end)
    load = {r["code"]: {d["day"]: d for d in r["days"]}
            for r in resource_load(conn, None, start, days)["resources"] if r["kind"] in ("seats", "beds")}

    def job(x) -> dict:
        return {"item_id": x["id"], "title": x["title"], "code": x["code"], "schedule": x["project"],
                "order": x["source_key"] if x["source"] != "schedule" else None, "assets": x["assets"], "area": x["area"],
                "start": x["start_at"], "end": x["end_at"], "quantity": x["quantity"]}

    out = []
    for n in range(days):
        a = start + timedelta(days=n)
        b = a + timedelta(days=1)
        day = a.date().isoformat()
        today = [x for x in rows if x["start_at"] < b and x["end_at"] > a and x["resource"] in res]
        crews, equipment = [], []
        for code, r in res.items():
            mine = [x for x in today if x["resource"] == code]
            if not mine or r["kind"] not in ("crew", *EQUIPMENT):
                continue
            entry = {"resource": code, "name": r["name"], "unit": r["unit"], "capacity": r["capacity"],
                     "jobs": [job(x) for x in mine]}
            (crews if r["kind"] == "crew" else equipment).append(entry | {"kind": r["kind"]})
        travel = [{"crew": m["crew"], "name": res[m["crew"]]["name"], "people": m["quantity"], "way": m["way"],
                   "schedule": m["project"], "by": by_seats[m["crew"]]["name"] if m["crew"] in by_seats else None}
                  for m in moves if m["start_at"] == a]
        limits = [{"resource": code, "name": res[code]["name"], "kind": res[code]["kind"], "unit": res[code]["unit"],
                   "need": d[day]["demand"], "capacity": d[day]["capacity"], "over": d[day]["demand"] > d[day]["capacity"] + EPS}
                  for code, d in load.items() if day in d and (d[day]["demand"] > 0)]
        aboard = [{"crew": t["crew"], "name": t["name"], "people": t["peak"], "schedule": t["project"]}
                  for t in stays if t["first"] < b and t["last"] > a
                  and not any(j["start_at"] < b and j["end_at"] > a for j in t["jobs"])]
        ship = materials.get(day, [])
        if crews or equipment or travel or limits or aboard or ship:
            out.append({"day": day, "crews": crews, "travel": travel, "aboard": aboard, "equipment": equipment,
                        "limits": limits, "materials": ship})
    return {"start": start, "end": end, "days": days, "by_day": out,
            "basis": "every active planned job's resource needs at its planned dates; a crew that a seats or beds "
                     "resource counts travels out the day its first job on a schedule starts and back the day its "
                     "last one finishes, and keeps its beds aboard in between; parts are the open reservations on "
                     "each work order, due the day its first planned job starts"}

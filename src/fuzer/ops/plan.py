"""The built-in integrated plan behind fuzer.core.plan: plan_item rows from every source on one timeline.

    propose / update             the contract (core/plan.py)
    items(conn, start, end)      the plan in a window, each item with its clashes and bundling chances
    sync_work_orders(conn)       open SAP work orders with dates in the register, as plan items
    sync_maintenance_plans(conn) the next due date of each SAP maintenance and inspection plan, as plan items
    plan_exception(conn, id, ...)  an exception the IOC chose to plan rather than work now

Clashes: two active items on one asset at the same time (simultaneous operations on it), and one person booked on
two items at once. Bundling: an item due within BUNDLE_DAYS of a turnaround or shutdown of its area could be
done in it (and one that needs the unit down should be).
"""

from datetime import date, datetime, time, timedelta, timezone

from ..core import events
from ..core.plan import KINDS, STATUSES, PlanError

ACTIVE = ("proposed", "scheduled", "in_progress")
SHUTDOWNS = ("turnaround", "shutdown")  # the items other work can be bundled into
BUNDLE_DAYS = 21
FIELDS = ("title", "kind", "start", "end", "status", "crew", "crew_hours", "needs_shutdown", "deferment_usd", "notes")


def _when(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime.combine(v, time(), timezone.utc)
    try:
        return _when(datetime.fromisoformat(str(v)))
    except ValueError as e:
        raise PlanError(f"not a date or time: {v!r}") from e


def _area(conn, subjects: list[str]) -> str | None:
    """The area of the first subject that is in one (itself, or up to four part_of hops)."""
    if not subjects:
        return None
    row = conn.execute(
        """
        with recursive up (n, fuzer_id, depth) as (
            select n, s, 0 from unnest(%s::text[]) with ordinality s (s, n)
            union all
            select up.n, e.dst, up.depth + 1 from up join edge e on e.src = up.fuzer_id and e.rel = 'part_of'
            where up.depth < 4)
        select up.fuzer_id from up join entity a on a.fuzer_id = up.fuzer_id and a.kind = 'area'
        order by up.n, up.depth limit 1
        """, (subjects,)).fetchone()
    return row["fuzer_id"] if row else None


def _check(kind: str | None, status: str | None, start: datetime | None, end: datetime | None) -> None:
    if kind is not None and kind not in KINDS:
        raise PlanError(f"kind must be one of {KINDS}")
    if status is not None and status not in STATUSES:
        raise PlanError(f"status must be one of {STATUSES}")
    if start and end and end < start:
        raise PlanError("an item can't end before it starts")


def propose(conn, kind: str, title: str, by: str, start, end, refs=(), source: str | None = None,
            source_key: str | None = None, crew: str | None = None, crew_hours: float | None = None,
            needs_shutdown: bool = False, deferment_usd: float | None = None, exception_id: int | None = None,
            notes: str | None = None, status: str = "proposed") -> dict:
    from .exceptions import match

    start, end = _when(start), _when(end)
    _check(kind, status, start, end)
    if not (title or "").strip():
        raise PlanError("a plan item needs a title")
    subjects, _ = match(conn, refs)
    row = conn.execute(
        """
        insert into plan_item (kind, title, source, source_key, subjects, area_id, start_at, end_at, status, crew,
                               crew_hours, needs_shutdown, deferment_usd, exception_id, notes, created_by, updated_by)
        values (%(kind)s, %(title)s, %(source)s, %(key)s, %(subjects)s, %(area)s, %(start)s, %(end)s, %(status)s,
                %(crew)s, %(hours)s, %(shutdown)s, %(deferment)s, %(exception)s, %(notes)s, %(by)s, %(by)s)
        on conflict (source, source_key) where source_key is not null do update set
            kind = excluded.kind, title = excluded.title, subjects = excluded.subjects, area_id = excluded.area_id,
            start_at = excluded.start_at, end_at = excluded.end_at, needs_shutdown = excluded.needs_shutdown,
            crew = coalesce(plan_item.crew, excluded.crew), crew_hours = coalesce(excluded.crew_hours, plan_item.crew_hours),
            deferment_usd = coalesce(excluded.deferment_usd, plan_item.deferment_usd),
            status = case when plan_item.status in ('done', 'cancelled') then plan_item.status else excluded.status end,
            updated_by = excluded.updated_by, updated_at = now()
        returning *, (xmax = 0) as created
        """, {"kind": kind, "title": title.strip(), "source": source or by, "key": source_key, "subjects": subjects,
              "area": _area(conn, subjects), "start": start, "end": end, "status": status, "crew": crew,
              "hours": crew_hours, "shutdown": needs_shutdown, "deferment": deferment_usd, "exception": exception_id,
              "notes": notes, "by": by}).fetchone()
    events.publish(conn, "plan.changed", f"plan_item:{row['id']}",
                   {"id": row["id"], "change": "proposed" if row["created"] else "updated", "by": by,
                    "title": row["title"], "start": row["start_at"].isoformat(), "end": row["end_at"].isoformat()})
    return row


def update(conn, item_id: int, by: str, **fields) -> dict:
    bad = set(fields) - set(FIELDS)
    if bad:
        raise PlanError(f"can't change {sorted(bad)}")
    row = conn.execute("select * from plan_item where id = %s for update", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    start = _when(fields["start"]) if fields.get("start") else row["start_at"]
    end = _when(fields["end"]) if fields.get("end") else row["end_at"]
    _check(fields.get("kind"), fields.get("status"), start, end)
    sets = {"start_at": start, "end_at": end, **{k: v for k, v in fields.items() if k not in ("start", "end")}}
    row = conn.execute(
        "update plan_item set " + ", ".join(f"{k} = %({k})s" for k in sets) +
        ", updated_by = %(by)s, updated_at = now() where id = %(id)s returning *", {**sets, "by": by, "id": item_id}).fetchone()
    events.publish(conn, "plan.changed", f"plan_item:{item_id}",
                   {"id": item_id, "change": "updated", "by": by, "fields": sorted(fields), "title": row["title"],
                    "start": row["start_at"].isoformat(), "end": row["end_at"].isoformat(), "status": row["status"]})
    return row


def _clashes(rows: list[dict]) -> dict[int, list[dict]]:
    """Clashes and bundling chances between the active items given, by item id."""
    out: dict[int, list[dict]] = {r["id"]: [] for r in rows}
    active = [r for r in rows if r["status"] in ACTIVE]
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            overlap = a["start_at"] < b["end_at"] and b["start_at"] < a["end_at"]
            shared = set(a["subjects"]) & set(b["subjects"])
            if overlap and shared:
                for x, y in ((a, b), (b, a)):
                    out[x["id"]].append({"kind": "same_asset", "with": y["id"], "title": y["title"],
                                         "say": f"at the same time as “{y['title']}” on the same asset"})
            person = a["crew"] and a["crew"] == b["crew"] and not a["crew"].startswith("role:")
            if overlap and person:
                for x, y in ((a, b), (b, a)):
                    out[x["id"]].append({"kind": "crew", "with": y["id"], "title": y["title"],
                                         "say": f"{x['crew']} is also on “{y['title']}” then"})
            for s, o in ((a, b), (b, a)):  # o could ride along with shutdown s (a turnaround or shutdown itself)
                if (s["kind"] in SHUTDOWNS and o["kind"] not in SHUTDOWNS and s["area_id"]
                        and s["area_id"] == o["area_id"] and not overlap
                        and abs((o["start_at"] - s["start_at"]).days) <= BUNDLE_DAYS):
                    say = "needs the unit down: do it during" if o["needs_shutdown"] else "could be done during"
                    out[o["id"]].append({"kind": "bundle", "with": s["id"], "title": s["title"],
                                         "say": f"{say} “{s['title']}” ({s['start_at']:%d %b})"})
    return out


def items(conn, start=None, end=None, area: str | None = None, status: str | None = None,
          subject: str | None = None, crew: str | None = None) -> list[dict]:
    """The plan between start and end (default: from a week ago to eight weeks ahead), in start order, each item
    with its subjects' and area's labels, its clashes and bundling chances. Items just outside the window that
    touch the same areas are compared too, so a clash at its edge still shows."""
    now = datetime.now(timezone.utc)
    start = _when(start) if start else now - timedelta(days=7)
    end = _when(end) if end else now + timedelta(days=56)
    margin = timedelta(days=BUNDLE_DAYS)
    rows = conn.execute(
        """
        select p.*, la.label as area_label,
               coalesce((select json_agg(json_build_object('fuzer_id', s, 'label', l.label) order by n)
                         from unnest(p.subjects) with ordinality u (s, n) left join entity_label l on l.fuzer_id = s),
                        '[]') as assets
        from plan_item p left join entity_label la on la.fuzer_id = p.area_id
        where p.start_at < %(end)s and p.end_at > %(start)s
          and (%(area)s::text is null or p.area_id = %(area)s)
          and (%(subject)s::text is null or %(subject)s = any(p.subjects))
        order by p.start_at, p.id
        """, {"start": start - margin, "end": end + margin, "area": area, "subject": subject}).fetchall()
    clashes = _clashes(rows)
    return [{**r, "clashes": clashes[r["id"]]} for r in rows
            if r["start_at"] < end and r["end_at"] > start and (status is None or r["status"] == status)
            and (crew is None or r["crew"] == crew)]


def coming_up(conn, person: str, days: int = 14) -> list[dict]:
    """What a field operator is planned onto over the next days, so the job a phone shows tomorrow is no surprise
    today: scheduled and started plan items with them, or a role they hold, as crew, in start order (proposals
    aren't commitments yet)."""
    from ..core import work

    crews = {person, *(f"role:{r}" for r in work.roles_of(conn, person))}
    now = datetime.now(timezone.utc)
    return [i for i in items(conn, now, now + timedelta(days=days))
            if i["crew"] in crews and i["status"] in ("scheduled", "in_progress")]


# ---- sources of plan items ---------------------------------------------------------------------------------------

OPEN_SAP = ("CRTD", "REL", "PCNF")


def sync_work_orders(conn, by: str = "sap") -> dict:
    """Open SAP work orders with dates (golden start / finish, else a day ending at basic_finish) as plan items:
    a revision means the order is planned for a turnaround, so it needs the shutdown. Orders that closed move to
    done."""
    rows = conn.execute(
        """
        select e.fuzer_id, jsonb_object_agg(g.field, g.value) as f,
               (select array_agg(distinct x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'concerns') as concerns,
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'work_order') as number
        from entity e join golden g on g.fuzer_id = e.fuzer_id
        where e.kind = 'work_order' and e.retired_at is null
        group by e.fuzer_id
        """).fetchall()
    added = updated = closed = skipped = 0
    for r in rows:
        f = {k: v for k, v in r["f"].items() if v not in (None, "")}
        key = r["number"] or r["fuzer_id"]
        status = str(f.get("status", "")).split()[0].upper() if f.get("status") else ""
        if status and status not in OPEN_SAP:
            closed += conn.execute("update plan_item set status = 'done', updated_by = %s, updated_at = now() "
                                   "where source = 'sap' and source_key = %s and status = any(%s)",
                                   (by, key, list(ACTIVE))).rowcount
            continue
        try:
            if f.get("start") and f.get("finish"):
                start, end = _when(f["start"]), _when(f["finish"])
            elif f.get("basic_finish"):
                end = _when(f["basic_finish"])
                start = end - timedelta(days=1)
            else:
                skipped += 1
                continue
        except PlanError:
            skipped += 1
            continue
        if end < start:
            start, end = end, start
        turnaround = bool(f.get("revision"))
        row = propose(conn, "turnaround" if turnaround else "maintenance",
                      str(f.get("text") or f.get("description") or f"Work order {key}"), by, start, end,
                      refs=[r["fuzer_id"], *(r["concerns"] or [])], source="sap", source_key=key,
                      needs_shutdown=turnaround, status="scheduled" if status == "REL" else "proposed",
                      notes=f"SAP order {key}" + (f", revision {f['revision']}" if turnaround else ""))
        added += row["created"]
        updated += not row["created"]
    return {"orders": len(rows), "added": added, "updated": updated, "closed": closed, "skipped": skipped}


def sync_maintenance_plans(conn, by: str = "sap") -> dict:
    """Each SAP maintenance or inspection plan's next due task as a proposed plan item on that day (INSP plans as
    inspections), one item per due date: when SAP rolls a plan forward, the new date is a new item and the old one,
    if nobody closed it, is marked done. Tasks that need the unit down are marked as needing a shutdown, so the
    plan shows them against the next turnaround."""
    rows = conn.execute(
        """
        select e.fuzer_id, jsonb_object_agg(g.field, g.value) as f,
               (select array_agg(distinct x.dst) from edge x where x.src = e.fuzer_id and x.rel = 'concerns') as concerns,
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'maintenance_plan') as number,
               (select l.label from entity_label l where l.fuzer_id = e.fuzer_id) as label
        from entity e join golden g on g.fuzer_id = e.fuzer_id
        where e.kind = 'maintenance_plan' and e.retired_at is null
        group by e.fuzer_id
        """).fetchall()
    added = updated = rolled = skipped = 0
    current = []
    for r in rows:
        f = {k: v for k, v in r["f"].items() if v not in (None, "")}
        plan_key = r["number"] or r["label"] or r["fuzer_id"]
        try:
            due = _when(str(f["next_due"])[:10])
        except (KeyError, PlanError):
            skipped += 1
            continue
        key = f"{plan_key}:{due:%Y-%m-%d}"
        current.append(key)
        inspection = str(f.get("kind", "")).upper().startswith("INSP")
        row = propose(conn, "inspection" if inspection else "maintenance",
                      str(f.get("task") or f.get("description") or f"Plan {plan_key}"), by, due, due + timedelta(days=1),
                      refs=r["concerns"] or [r["fuzer_id"]], source="sap_plan", source_key=key,
                      needs_shutdown=f.get("needs_shutdown") == "X",
                      notes=f"SAP {'inspection' if inspection else 'maintenance'} plan {plan_key}"
                            + (f", every {f['interval_days']:g} days" if isinstance(f.get("interval_days"), (int, float)) else ""))
        added += row["created"]
        updated += not row["created"]
    if current:  # a due date SAP has moved past: done there, so done here
        rolled = conn.execute(
            "update plan_item set status = 'done', updated_by = %s, updated_at = now() "
            "where source = 'sap_plan' and status = any(%s) and source_key <> all(%s) "
            "and regexp_replace(source_key, ':[^:]*$', '') = any(%s)",
            (by, list(ACTIVE), current, [k.rsplit(":", 1)[0] for k in current])).rowcount
    return {"plans": len(rows), "added": added, "updated": updated, "rolled_forward": rolled, "skipped": skipped}


def plan_exception(conn, exception_id: int, by: str, start, end, crew: str | None = None,
                   needs_shutdown: bool = False, notes: str | None = None) -> dict:
    """Plan an exception rather than work it now: a plan item on its assets, linked to it."""
    e = conn.execute("select * from exception_record where id = %s", (exception_id,)).fetchone()
    if e is None:
        raise PlanError(f"no exception {exception_id}")
    row = propose(conn, "exception", e["title"], by, start, end, refs=e["subjects"], source="exceptions",
                  source_key=str(exception_id), crew=crew, needs_shutdown=needs_shutdown,
                  deferment_usd=e["value_usd"], exception_id=exception_id, notes=notes, status="scheduled")
    events.publish(conn, "exception.planned", f"exception:{exception_id}",
                   {"id": exception_id, "plan_item": row["id"], "by": by, "start": row["start_at"].isoformat()})
    return row

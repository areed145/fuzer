"""The built-in integrated plan behind fuzer.core.plan: plan_item rows from every source on one timeline.

    propose / update             the contract (core/plan.py)
    items(conn, start, end)      the plan in a window, each item with its clashes and bundling chances
    sync_work_orders(conn)       open SAP work orders with dates in the register, as plan items
    sync_maintenance_plans(conn) the next due date of each SAP maintenance and inspection plan, as plan items
    plan_exception(conn, id, ...)  an exception the IOC chose to plan rather than work now
    link_case(conn, id, item)    the case working an exception, on the bars it was planned as (a bar is its case's job)
    book(conn, id, by, resource) book a job on a crew (or any resource): checked against capacity, over it only with a reason
    book_dispatch(conn, ...)     work sent to the field now takes a day of its crew, on the same capacity as planned work
    finish_closed_work(conn)     bars whose work closed are done (or cancelled with a cancelled case), never left behind

Clashes: two active items on one asset at the same time (simultaneous operations on it), and one person booked on
two items at once. Bundling: an item due within BUNDLE_DAYS of a turnaround or shutdown of its area could be
done in it (and one that needs the unit down should be). Readiness: active work starting within READY_DAYS lists
its assets' spare parts recorded with zero stock (`parts_short`), so stores can be asked before the crew turns up.
"""

from datetime import UTC, date, datetime, time, timedelta

from ..core import clock, events
from ..core.plan import KINDS, STATUSES, PlanError, PlanNotReady, PlanOverload

ACTIVE = ("proposed", "scheduled", "in_progress")
SHUTDOWNS = ("turnaround", "shutdown")  # the items other work can be bundled into
READY_DAYS = 30  # how far ahead planned work is checked for spare parts out of stock
BUNDLE_DAYS = 21
FIELDS = ("title", "kind", "start", "end", "status", "crew", "crew_hours", "needs_shutdown", "deferment_usd", "notes",
          "place")
PLACES = ("field", "desk")  # where a job is done: field jobs go through Ready and Release, desk jobs do not


def _when(v) -> datetime:
    if isinstance(v, datetime):
        return clock.aware(v)
    if isinstance(v, date):
        return datetime.combine(v, time(), clock.zone())
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
            start_at = case when plan_item.moved_by is null then excluded.start_at else plan_item.start_at end,
            end_at = case when plan_item.moved_by is null then excluded.end_at else plan_item.end_at end,
            needs_shutdown = excluded.needs_shutdown,
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
    if fields.get("place") is not None and fields["place"] not in PLACES:
        raise PlanError(f"place must be one of {PLACES}")
    sets = {"start_at": start, "end_at": end, **{k: v for k, v in fields.items() if k not in ("start", "end")}}
    if (start, end) != (row["start_at"], row["end_at"]):
        sets["moved_by"] = by  # a person's dates now win over the source's next proposal
    row = conn.execute(
        "update plan_item set " + ", ".join(f"{k} = %({k})s" for k in sets) +
        ", updated_by = %(by)s, updated_at = now() where id = %(id)s returning *", {**sets, "by": by, "id": item_id}).fetchone()
    if fields.get("status") == "cancelled" and row["exception_id"]:
        _off_the_plan(conn, row, by)
    events.publish(conn, "plan.changed", f"plan_item:{item_id}",
                   {"id": item_id, "change": "updated", "by": by, "fields": sorted(fields), "title": row["title"],
                    "start": row["start_at"].isoformat(), "end": row["end_at"].isoformat(), "status": row["status"]})
    return row


def _off_the_plan(conn, row: dict, by: str) -> None:
    """A bar taken off the plan while its work is still open: the work goes back to waiting for a decision, and its
    case, when it has one, says who took it off."""
    e = conn.execute("select state, item_id from exception_record where id = %s", (row["exception_id"],)).fetchone()
    if e and e["state"] == "open" and e["item_id"]:
        from ..core import work
        work.comment(conn, e["item_id"], by, f"Taken off the plan ({row['start_at']:%d %b}); the work is still open.")


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
    now = datetime.now(UTC)
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
    from . import simops

    for item_id, found in simops.on_plan(conn, rows).items():  # hazards side by side in one area (P5.11b)
        clashes[item_id].extend(found)
    shown = [r for r in rows if r["start_at"] < end and r["end_at"] > start and (status is None or r["status"] == status)
             and (crew is None or r["crew"] == crew)]
    short = _parts_short(conn, shown, now)
    return [{**r, "clashes": clashes[r["id"]], "parts_short": short.get(r["id"], [])} for r in shown]


def _parts_short(conn, rows: list[dict], now: datetime) -> dict[int, list[dict]]:
    """For active items starting within READY_DAYS (and not yet over): the spare parts of their assets whose
    recorded stock is zero, by item id. A part with no stock record is not reported (unknown is not short), and
    a stock count is not a reservation: this says what to check with stores, not what is booked."""
    from ..graph import queries
    by_asset: dict[str, list[dict]] = {}
    out: dict[int, list[dict]] = {}
    for r in rows:
        if r["status"] not in ACTIVE or r["end_at"] < now or r["start_at"] > now + timedelta(days=READY_DAYS):
            continue
        for s in r["subjects"]:
            if s not in by_asset:
                by_asset[s] = [{"id": p["fuzer_id"], "label": p["label"], "description": p["description"],
                                "mpn": p["mpn"], "bin": p["bin"], "for": s} for p in queries.spares(conn, s)
                               if p["stock"] is not None and p["stock"] <= 0]
            out.setdefault(r["id"], []).extend(by_asset[s])
    return out


def coming_up(conn, person: str, days: int = 14) -> list[dict]:
    """What a field operator is planned onto over the next days, so the job a phone shows tomorrow is no surprise
    today: scheduled and started plan items with them, or a role they hold, as crew, in start order (proposals
    aren't commitments yet)."""
    from ..core import work
    from .routing import TO_DISPATCH

    crews = {person, *(f"role:{r}" for r in work.roles_of(conn, person))}
    now = datetime.now(UTC)
    mine = [i for i in items(conn, now, now + timedelta(days=days))
            if i["crew"] in crews and i["status"] in ("scheduled", "in_progress")]
    # a job whose case was sent shows on their field jobs instead; one whose case still waits to be sent shows here
    sent = {r["id"] for r in conn.execute("select id from work_item where id = any(%s) and step is distinct from %s",
                                          ([i["item_id"] for i in mine if i.get("item_id")], TO_DISPATCH))}
    return [i for i in mine if i.get("item_id") not in sent]


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
               (select min(k.key) from entity_key k where k.fuzer_id = e.fuzer_id and k.kind = 'work_order') as number,
               (select min(l.label) from edge x join entity_label l on l.fuzer_id = x.dst
                where x.src = e.fuzer_id and x.rel = 'done_by') as work_center
        from entity e join golden g on g.fuzer_id = e.fuzer_id
        where e.kind = 'work_order' and e.retired_at is null
        group by e.fuzer_id
        """).fetchall()
    crews = {r["work_center"]: r["code"] for r in conn.execute(
        "select code, work_center from plan_resource where kind = 'crew' and work_center is not null")}
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
        # The crew it takes counts against the same pool a schedule levels against (P5.18), unless the order is a
        # schedule's summary: its operations carry the need then, and counting both would count it twice.
        crew = crews.get(r["work_center"])
        if crew and isinstance(f.get("people"), (int, float)) and f["people"] > 0 and not row.get("project"):
            conn.execute("insert into plan_demand (item_id, resource, quantity) values (%s, %s, %s) "
                         "on conflict (item_id, resource) do update set quantity = excluded.quantity",
                         (row["id"], crew, f["people"]))
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
    from ..sources.relative_dates import offset

    added = updated = rolled = skipped = 0
    current = []
    written = timedelta(days=offset())  # a rolling facility's dates move on daily (#1085): key on the date as written
    for r in rows:
        f = {k: v for k, v in r["f"].items() if v not in (None, "")}
        plan_key = r["number"] or r["label"] or r["fuzer_id"]
        try:
            due = _when(str(f["next_due"])[:10])
        except (KeyError, PlanError):
            skipped += 1
            continue
        key = _same_due(conn, plan_key, due - written, by)
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


DUE_SLACK = timedelta(days=3)  # a due date "as written" a few days off the one on record is the same due date


def _same_due(conn, plan_key: str, written: datetime, by: str) -> str:
    """The key of a plan's item for the due date `written`. A rolling facility's dates are rolled when they load and
    keyed when the plan syncs, so a load and a sync either side of midnight write one due date a day apart: the item
    already there within a few days is the same due date, under its own key. One the sync closed as rolled past is
    open again; one more for the same date, made by the sync and never touched by a person, is a duplicate and goes
    (else it is cancelled). Without this the plan showed one inspection two or three times, every copy closed, and
    the production-at-risk card said no shutdown was planned above a shutdown bar."""
    key = f"{plan_key}:{written:%Y-%m-%d}"
    near = []
    for r in conn.execute(
            "select id, source_key, status, updated_by, moved_by from plan_item where source = 'sap_plan' "
            "and regexp_replace(source_key, ':[^:]*$', '') = %s order by id", (plan_key,)).fetchall():
        try:
            day = datetime.fromisoformat(r["source_key"].rsplit(":", 1)[1]).replace(tzinfo=written.tzinfo)
        except ValueError:
            continue
        if abs(day - written) <= DUE_SLACK:
            near.append(r)
    if not near:
        return key
    keep = next((r for r in near if r["source_key"] == key), None) or near[0]
    if keep["status"] == "done" and keep["updated_by"] == by:
        conn.execute("update plan_item set status = 'proposed', updated_at = now() where id = %s", (keep["id"],))
    for r in near:
        if r["id"] == keep["id"]:
            continue
        if r["updated_by"] == by and r["moved_by"] is None:
            conn.execute("delete from plan_item where id = %s", (r["id"],))
        elif r["status"] not in ("done", "cancelled"):
            conn.execute("update plan_item set status = 'cancelled', updated_by = %s, updated_at = now() "
                         "where id = %s", (by, r["id"]))
    return keep["source_key"]


def plan_exception(conn, exception_id: int, by: str, start, end, crew: str | None = None,
                   needs_shutdown: bool = False, notes: str | None = None) -> dict:
    """Plan an exception rather than work it now: a plan item on its assets, linked to it."""
    e = conn.execute("select * from exception_record where id = %s", (exception_id,)).fetchone()
    if e is None:
        raise PlanError(f"no exception {exception_id}")
    row = propose(conn, "exception", e["title"], by, start, end, refs=e["subjects"], source="exceptions",
                  source_key=str(exception_id), crew=crew, needs_shutdown=needs_shutdown,
                  deferment_usd=e["value_usd"], exception_id=exception_id, notes=notes, status="scheduled")
    if e["item_id"]:
        row = next((r for r in link_case(conn, exception_id, e["item_id"]) if r["id"] == row["id"]), row)
    events.publish(conn, "exception.planned", f"exception:{exception_id}",
                   {"id": exception_id, "plan_item": row["id"], "by": by, "start": row["start_at"].isoformat()})
    return row


def link_case(conn, exception_id: int, item_id: int) -> list[dict]:
    """The case that works an exception, on the bars it was planned as: from then on the bar is that case's job."""
    return conn.execute("update plan_item set item_id = %s where exception_id = %s and item_id is distinct from %s "
                        "returning *", (item_id, exception_id, item_id)).fetchall()


def finish_closed_work(conn, exception_ids: list[int] | None = None, by: str = "fuzer") -> list[dict]:
    """Close the bars of work that is over: an active plan item whose case is done (or whose exception is no longer
    open: closed, or the condition cleared) is done, and cancelled when its case was cancelled. Without this a closed
    case left its bar scheduled, and Plan, Logistics and crew load counted a job nobody would do (step 1 of the work
    model). `exception_ids` limits it to those; with none every bar is checked, so the work tick sweeps up the rest."""
    rows = conn.execute(
        """
        with over as (
            select p.id, case when w.state = 'cancelled' then 'cancelled' else 'done' end as status
            from plan_item p
            left join exception_record e on e.id = p.exception_id
            left join work_item w on w.id = coalesce(p.item_id, e.item_id)
            where p.status = any(%(active)s)
              and ((e.id is not null and e.state <> 'open') or w.state in ('done', 'cancelled'))
              and (%(ids)s::bigint[] is null or e.id = any(%(ids)s)))
        update plan_item p set status = over.status, updated_by = %(by)s, updated_at = now()
        from over where p.id = over.id
        returning p.id, p.title, p.status, p.start_at, p.end_at, p.exception_id, p.item_id
        """, {"by": by, "active": list(ACTIVE), "ids": exception_ids}).fetchall()
    for r in rows:
        events.publish(conn, "plan.changed", f"plan_item:{r['id']}",
                       {"id": r["id"], "change": "updated", "by": by, "fields": ["status"], "title": r["title"],
                        "start": r["start_at"].isoformat(), "end": r["end_at"].isoformat(), "status": r["status"],
                        "reason": "its work closed"})
    return rows


DISPATCH_HOURS = 4.0  # what work sent to the field now is booked for on its crew, until it comes back


def crew_for(conn, subjects: list[str]) -> dict | None:
    """The crew resource that does a job's work: the plan resource of kind crew on the work center of its equipment's
    work orders (the same work center the ready check reads), if one is set up."""
    from .readiness import _centers

    labels = [c["label"] for c in _centers(conn, subjects) if c["label"]]
    if not labels:
        return None
    return conn.execute("select * from plan_resource where kind = 'crew' and work_center = any(%s) order by code limit 1",
                        (labels,)).fetchone()


def _overloads(conn, resource: str, item: dict) -> list[dict]:
    """The runs of hours over capacity on `resource` that include `item`, as the plan stands in this transaction."""
    from . import schedule

    start = item["start_at"].astimezone(clock.zone())
    days = max(1, (item["end_at"] - item["start_at"]).days + 2)
    load = schedule.resource_load(conn, resource, start, days)
    return [o for r in load["resources"] for o in r["overloads"]
            if any(j["item_id"] == item["id"] for j in o["jobs"])]


def book(conn, item_id: int, by: str, resource: str, quantity: float = 1, person: str | None = None,
         reason: str | None = None) -> dict:
    """Book a job on a resource, usually a crew: `quantity` of it for the job's dates, and optionally the person (or
    `role:<role>`) it goes to. Checked against the resource's capacity with every other source's demand on those days;
    a booking that takes it over is refused (PlanOverload, naming the overload) unless `reason` says why it goes
    anyway, and the reason is kept on the job's case or its notes. A proposed job becomes scheduled."""
    row = conn.execute("select * from plan_item where id = %s for update", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    res = conn.execute("select * from plan_resource where code = %s", (resource,)).fetchone()
    if res is None:
        raise PlanError(f"no resource {resource}")
    if not quantity or quantity <= 0:
        raise PlanError("book at least one")
    conn.execute("insert into plan_demand (item_id, resource, quantity) values (%s, %s, %s) "
                 "on conflict (item_id, resource) do update set quantity = excluded.quantity", (item_id, resource, quantity))
    over = _overloads(conn, resource, row)
    reason = (reason or "").strip()
    if over and not reason:
        worst = max(over, key=lambda o: o["over_by"])
        others = ", ".join(j["title"] for j in worst["jobs"] if j["item_id"] != item_id)[:200]
        raise PlanOverload(f"{res['name']} would be over capacity: {worst['peak']:g} of {worst['capacity']:g} "
                           f"from {worst['from']:%a %d %b %H:%M}" + (f", with {others}" if others else "")
                           + ". Say why it should be booked anyway.")
    fields = {"status": "scheduled"} if row["status"] == "proposed" else {}
    if person is not None:
        fields["crew"] = person.strip() or None
    if fields:
        update(conn, item_id, by, **fields)
    if over:
        note = f"Booked on {res['name']} over its capacity. Why: {reason}"
        if row["item_id"]:
            from ..core import work
            work.comment(conn, row["item_id"], by, note)
        else:
            conn.execute("update plan_item set notes = concat_ws(' · ', notes, %s::text) where id = %s", (note, item_id))
    events.publish(conn, "plan.changed", f"plan_item:{item_id}",
                   {"id": item_id, "change": "booked", "by": by, "resource": resource, "quantity": quantity,
                    "over_capacity": bool(over), "title": row["title"]})
    return {**conn.execute("select * from plan_item where id = %s", (item_id,)).fetchone(), "over_capacity": over}


def book_dispatch(conn, exception_id: int, item_id: int, to: str, by: str) -> dict | None:
    """Work sent to the field now (an urgent dispatch) takes its crew's time like planned work does: its bar starts now,
    runs DISPATCH_HOURS and books one of the crew that does the equipment's work, so Plan, Resources, Logistics and the
    next dispatch's ready check all see it. A bar it was already planned as is brought forward to now. The bar closes
    with the case."""
    e = conn.execute("select * from exception_record where id = %s", (exception_id,)).fetchone()
    if e is None:
        return None
    now = clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=clock.zone())
    end = now + timedelta(hours=DISPATCH_HOURS)
    bar = conn.execute("select * from plan_item where source = 'exceptions' and source_key = %s",
                       (str(exception_id),)).fetchone()
    if bar is None:
        bar = propose(conn, "exception", e["title"], by, now, end, refs=e["subjects"], source="exceptions",
                      source_key=str(exception_id), crew=to, exception_id=exception_id, status="in_progress",
                      deferment_usd=e["value_usd"])
    elif bar["status"] not in ACTIVE or bar["start_at"] > now:
        bar = conn.execute("update plan_item set status = 'in_progress', start_at = %s, end_at = greatest(end_at, %s), "
                           "crew = %s, updated_by = %s, updated_at = now() where id = %s returning *",
                           (now, end, to, by, bar["id"])).fetchone()
    conn.execute("update plan_item set item_id = %s where id = %s", (item_id, bar["id"]))
    crew = crew_for(conn, list(e["subjects"] or []))
    if crew and not conn.execute("select 1 from plan_demand where item_id = %s", (bar["id"],)).fetchone():
        conn.execute("insert into plan_demand (item_id, resource, quantity) values (%s, %s, 1)", (bar["id"], crew["code"]))
    return conn.execute("select * from plan_item where id = %s", (bar["id"],)).fetchone()


# ---- prerequisites decide Ready; field work reaches the field through Release (step 5 of docs/work-model.md) --------

def _shutdown_cover(conn, row: dict) -> bool:
    """Whether a turnaround or shutdown on the plan covers a job that needs the unit down: same area, overlapping."""
    return bool(conn.execute(
        "select 1 from plan_item where kind = any(%s) and status = any(%s) and id <> %s and area_id is not distinct "
        "from %s and start_at <= %s and end_at >= %s limit 1",
        (list(SHUTDOWNS), list(ACTIVE), row["id"], row["area_id"], row["start_at"], row["end_at"])).fetchone())


def prerequisites(conn, item_id: int) -> dict:
    """What a job needs before it can go: each prerequisite satisfied or not, with what holds it. Scheduled (booked
    on a crew or given to someone), each ready check (parts, crew capacity, permits, SIMOPS, MOCs), no clash on the
    plan, and the unit down when the job needs it. A new kind of check is a new prerequisite, not a change to the
    gate. Ready is all of them satisfied."""
    from . import readiness

    row = conn.execute("select * from plan_item where id = %s", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    booked = row["status"] in ("scheduled", "in_progress") or bool(conn.execute(
        "select 1 from plan_demand where item_id = %s limit 1", (item_id,)).fetchone())
    out = [{"kind": "scheduled", "label": "Scheduled", "satisfied": booked,
            "detail": [] if booked else ["not booked on a crew yet"]}]
    checks = readiness.check(conn, list(row["subjects"] or []), row["exception_id"])["checks"]
    for c in checks:
        out.append({"kind": c.get("check") or c["label"].lower(), "label": c["label"],
                    "satisfied": not c["blockers"], "detail": list(c["blockers"])})
    others = conn.execute("select * from plan_item where status = any(%s) and start_at < %s and end_at > %s",
                          (list(ACTIVE), row["end_at"], row["start_at"])).fetchall()
    clashes = [c["say"] for c in _clashes([row, *[o for o in others if o["id"] != row["id"]]])[row["id"]]
               if c["kind"] in ("same_asset", "crew")]
    out.append({"kind": "clash", "label": "No clash", "satisfied": not clashes, "detail": clashes})
    if row["needs_shutdown"]:
        down = _shutdown_cover(conn, row)
        out.append({"kind": "unit_down", "label": "Unit down", "satisfied": down,
                    "detail": [] if down else ["needs the unit down and no shutdown on the plan covers it"]})
    return {"item_id": item_id, "ready": all(p["satisfied"] for p in out), "prerequisites": out,
            "released_at": row["released_at"], "released_by": row["released_by"]}


def _note(conn, row: dict, by: str, text: str) -> None:
    """On the job's case when it has one, else on its notes."""
    if row["item_id"]:
        from ..core import work
        work.comment(conn, row["item_id"], by, text)
    else:
        conn.execute("update plan_item set notes = concat_ws(' · ', notes, %s::text) where id = %s", (text, row["id"]))


def release(conn, item_id: int, by: str, reason: str | None = None) -> dict:
    """Release a job to the field: the one way planned field work reaches it. Its prerequisites are checked; one still
    open is refused (PlanNotReady, naming it) unless `reason` says why it goes anyway, and the reason is kept on the
    case. A job whose case waits to be sent is sent to its crew (the operator queue when it has none); the bar keeps
    its dates and its booking."""
    from ..core import gates

    gates.require_person(conn, "field_dispatch", by, ref=f"plan_item:{item_id}")
    row = conn.execute("select * from plan_item where id = %s for update", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    if row["status"] not in ACTIVE:
        raise PlanError(f"plan item {item_id} is {row['status']}: only open work is released")
    if row["released_at"]:
        raise PlanError(f"plan item {item_id} was released {row['released_at']:%d %b %H:%M} by {row['released_by']}")
    if row["place"] == "desk":
        raise PlanError(f"plan item {item_id} is done at a desk: nothing goes to the field")
    pre = prerequisites(conn, item_id)
    open_ = [f"{p['label']}: {'; '.join(p['detail']) or 'not satisfied'}" for p in pre["prerequisites"]
             if not p["satisfied"]]
    reason = (reason or "").strip()
    if open_ and not reason:
        raise PlanNotReady("Not ready: " + " · ".join(open_)[:400] + ". Say why it should be released anyway.")
    row = conn.execute(
        "update plan_item set released_at = now(), released_by = %s, release_reason = %s, "
        "status = case when status = 'proposed' then 'scheduled' else status end, updated_by = %s, updated_at = now() "
        "where id = %s returning *", (by, reason or None, by, item_id)).fetchone()
    if row["exception_id"]:
        from . import exceptions, routing

        e = conn.execute("select * from exception_record where id = %s", (row["exception_id"],)).fetchone()
        if e and e["state"] == "open" and (not e["item_id"] or routing.waiting_case(conn, e["item_id"])):
            exceptions.dispatch(conn, e["id"], by, row["crew"] or f"role:{exceptions.OPERATOR_ROLE}",
                                f"Released from the plan for {row['start_at']:%a %d %b}", override=reason or None,
                                blockers=open_, planned=True)
            row = conn.execute("select * from plan_item where id = %s", (item_id,)).fetchone()
    if reason and not row["exception_id"]:
        _note(conn, row, by, f"Released though not ready ({'; '.join(open_)}). Why: {reason}")
    events.publish(conn, "plan.changed", f"plan_item:{item_id}",
                   {"id": item_id, "change": "released", "by": by, "title": row["title"], "ready": not open_,
                    "reason": reason or None})
    return {**row, "prerequisites": pre["prerequisites"]}


def reschedule(conn, item_id: int, by: str, start, end, reason: str) -> dict:
    """Move a job to new dates with the reason kept on its case (or notes). A released job moved goes back to waiting
    for release: what was ready then is checked again for the new dates."""
    reason = (reason or "").strip()
    if not reason:
        raise PlanError("say why it moves")
    row = conn.execute("select * from plan_item where id = %s", (item_id,)).fetchone()
    if row is None:
        raise PlanError(f"no plan item {item_id}")
    moved = update(conn, item_id, by, start=start, end=end)
    if row["released_at"]:
        moved = conn.execute("update plan_item set released_at = null, released_by = null, release_reason = null "
                             "where id = %s returning *", (item_id,)).fetchone()
    _note(conn, moved, by, f"Rescheduled from {row['start_at']:%a %d %b} to {moved['start_at']:%a %d %b}. Why: {reason}"
          + (" It was released: release it again for the new dates." if row["released_at"] else ""))
    return moved

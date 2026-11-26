"""Ready to dispatch (P5.5, #680): one check before a job is sent. It reads the records the job's equipment has, never
its equipment type:

    spares   the parts reserved on the job's own work order (SAP `reserves`: the order among its subjects, or the
             order of the plan item that works it) against stock, with open purchase orders and their delivery dates
             (P5.5a, #1391): a part short is a blocker; one on order names the date it is due. Never the
             equipment's BOM: a cracked level glass does not wait on the vessel's seal kit. A job with no order has
             no parts to check. A part with no stock level is unknown, never 0 in stock: the check says why (no
             stock source) and what would answer it (P6.6)
    crew     the work center (SAP `arbpl`) that does the equipment's work orders, its crew size, and the jobs
             already sent to it and still open (P5.5b): a full crew is a blocker naming the jobs holding it. A work
             center with a crew resource on the plan is read from the plan's one capacity ledger instead: everyone
             booked on it today, planned or sent out (step 3 of the work model). Work whose words name a trade
             (a transmitter or gauge is instrument work, a motor or switchgear electrical) is checked against that
             trade's work center only, not every crew that has worked on the equipment (#1365)
    field    what the field's latest report says the job waits on: a note or could-not-do naming a gas test, a
             permit, an isolation, depressurising, a scaffold or parts holds it until a later report (#1365)

A check with no records to read says so and does not block. A blocked job is sent only with a reason (enforced where
people dispatch: the dispatch route and card), and the reason goes on the case's history. A send to the maintenance
planner is never held: planning the parts and the crew is the planner's work (#1391).
"""

import re
from datetime import UTC, date, datetime

from ..core import clock


def _units(conn, subjects: list[str]) -> list[str]:
    """The equipment behind a job's subjects: each subject and whatever is installed at it now."""
    if not subjects:
        return []
    return [r["id"] for r in conn.execute(
        "select unnest(%(s)s::text[]) as id union select src from edge where rel = 'installed_at' "
        "and dst = any(%(s)s) and valid_to is null", {"s": subjects}).fetchall()]


def orders(conn, job: int | None = None, plan_item: int | None = None) -> list[str]:
    """The work orders a job is done under: an order among its exception's subjects, and the SAP order of each plan
    item that works it (the plan item itself, one planned for its exception, or one on the same case)."""
    if job is None and plan_item is None:
        return []
    return [r["id"] for r in conn.execute(
        """
        with keys as (
            select p.source_key as k from plan_item p
            where p.source = 'sap' and p.source_key is not null
              and (p.id = %(item)s or p.exception_id = %(job)s
                   or p.item_id in (select item_id from exception_record where id = %(job)s)
                   or p.item_id in (select item_id from plan_item where id = %(item)s))
        )
        select o.fuzer_id as id from entity o
        where o.kind = 'work_order' and o.retired_at is null
          and (o.fuzer_id in (select unnest(subjects) from exception_record where id = %(job)s)
               or o.fuzer_id in (select k from keys)
               or o.fuzer_id in (select fuzer_id from entity_key where kind = 'work_order' and key in (select k from keys)))
        order by 1
        """, {"job": job, "item": plan_item}).fetchall()]


def spares(conn, work_orders: list[str], today: date | None = None) -> dict:
    """The parts reserved on a job's work orders: needed, in stock, on order. Ready when every part is in stock."""
    today = today or clock.today()
    rows = conn.execute(
        """
        select b.dst as material, l.label, gd.value #>> '{}' as description,
               sum(coalesce((b.props->>'quantity')::numeric, 1)) as needed,
               (gs.value #>> '{}')::numeric as stock,
               (select coalesce(jsonb_agg(jsonb_build_object(
                         'po', pl.label, 'quantity', (pq.value #>> '{}')::numeric, 'due', pd.value #>> '{}')
                         order by pd.value #>> '{}'), '[]')
                from edge o join entity po on po.fuzer_id = o.src and po.kind = 'purchase_order'
                left join entity_label pl on pl.fuzer_id = po.fuzer_id
                left join golden pq on pq.fuzer_id = po.fuzer_id and pq.field = 'quantity'
                left join golden pd on pd.fuzer_id = po.fuzer_id and pd.field = 'delivery_date'
                left join golden ps on ps.fuzer_id = po.fuzer_id and ps.field = 'status'
                where o.rel = 'orders' and o.dst = b.dst and coalesce(ps.value #>> '{}', 'open') = 'open') as orders
        from edge b
        left join entity_label l on l.fuzer_id = b.dst
        left join golden gd on gd.fuzer_id = b.dst and gd.field = 'description'
        left join golden gs on gs.fuzer_id = b.dst and gs.field = 'stock'
        where b.rel = 'reserves' and b.src = any(%s) and b.valid_to is null
          and coalesce(b.props->>'status', 'open') = 'open'
        group by b.dst, l.label, gd.value, gs.value
        order by l.label
        """, (list(work_orders),)).fetchall() if work_orders else []
    parts, blockers, unstocked = [], [], []
    for r in rows:
        name = r["description"] or r["label"] or r["material"]
        needed, stock = float(r["needed"]), None if r["stock"] is None else float(r["stock"])
        short = None if stock is None else max(0.0, needed - stock)
        coming = [o for o in r["orders"] if o["due"]]
        part = {"material": r["material"], "label": r["label"], "description": r["description"], "needed": needed,
                "stock": stock, "short": short, "orders": coming}
        parts.append(part)
        if stock is None:  # unknown, never 0: no stock level to read it from
            unstocked.append(name)
        elif short:
            due = next((o for o in coming if (o["quantity"] or 0) >= short), coming[0] if coming else None)
            blockers.append(f"{name}: {_n(stock)} in stock, {_n(needed)} needed; "
                            + (f"on order ({due['po']}), due {due['due']}" if due else "nothing on order"))
    out = {"check": "spares", "label": "Spares", "ready": not blockers, "blockers": blockers, "parts": parts,
           "checked": bool(rows), "note": None if rows else "No parts reserved on its work order, so none to check."
           if work_orders else "No work order on this job, so no parts to check."}
    if unstocked:
        return {**out, **_unstocked(conn, unstocked, len(rows))}
    return out


NO_STOCK = "no stock source"


def _unstocked(conn, names: list[str], of: int) -> dict:
    """Parts with no stock level: unknown with its reason, and what would answer it, never read as none in stock."""
    from . import undetermined

    loaded = conn.execute("select 1 from golden where field = 'stock' limit 1").fetchone() is not None
    why = f"no stock level for {', '.join(names)}" if loaded else NO_STOCK
    return {"checked": False, "unknown": why,
            "note": f"Parts readiness unknown: {why}." if len(names) == of else
                    f"Parts readiness unknown for {len(names)} of {of} parts: {why}.",
            "undetermined": [undetermined.note("Are the parts for this job in stock?", "no_data", "source",
                                               "stock levels", f"parts readiness unknown: {why}", "readiness")]}


OPEN_ORDER = ("CRTD", "REL", "PCNF")  # SAP order statuses still to be done


def _centers(conn, subjects: list[str]) -> list[dict]:
    """The work centers that do a job's work: a work order's own, else those of the equipment's open work orders,
    else of its latest one."""
    units = _units(conn, subjects)
    if not units:
        return []
    rows = conn.execute(
        """
        with orders as (
            select wo.fuzer_id as wo, d.dst as center,
                   coalesce(st.value #>> '{}', '') = any(%(open)s) as open,
                   coalesce(gs.value #>> '{}', gf.value #>> '{}', '') as at,
                   wo.fuzer_id = any(%(u)s) as own
            from entity wo
            join edge d on d.src = wo.fuzer_id and d.rel = 'done_by'
            left join golden st on st.fuzer_id = wo.fuzer_id and st.field = 'status'
            left join golden gs on gs.fuzer_id = wo.fuzer_id and gs.field = 'start'
            left join golden gf on gf.fuzer_id = wo.fuzer_id and gf.field = 'basic_finish'
            where wo.kind = 'work_order' and (wo.fuzer_id = any(%(u)s) or exists (
                select 1 from edge c where c.src = wo.fuzer_id and c.rel = 'concerns' and c.dst = any(%(u)s)))
        ), picked as (
            select distinct center from orders
            where own or (not exists (select 1 from orders where own) and (open or (
                  not exists (select 1 from orders where open)
                  and at = (select max(at) from orders)))))
        select p.center as id, l.label, gd.value #>> '{}' as description, (gc.value #>> '{}')::numeric as crew
        from picked p
        left join entity_label l on l.fuzer_id = p.center
        left join golden gd on gd.fuzer_id = p.center and gd.field = 'description'
        left join golden gc on gc.fuzer_id = p.center and gc.field = 'crew_size'
        order by l.label
        """, {"u": units, "open": list(OPEN_ORDER)}).fetchall()
    return [dict(r) for r in rows]


# The trade a job's own words call for, and how its work center describes itself: a level transmitter drifting on a
# separator is the instrument technicians' work, whoever does the separator's mechanical work orders.
TRADES = {
    "instrument": (re.compile(r"(?i)transmitter|gauge|instrument|calibrat|analy[sz]er|cable gland|local display"
                              r"|impulse line|positioner|\bdcs\b"), re.compile(r"(?i)instrument|^inst")),
    "electrical": (re.compile(r"(?i)\bmotor\b|electrical|switchgear|batter(y|ies)"), re.compile(r"(?i)electric|^elec")),
}


def _trade_centers(conn, centers: list[dict], job: int | None) -> list[dict]:
    """Of the work centers that do the equipment's work, the one the job's own words need: an instrument job goes to
    the instrument crew even where the equipment's orders went to the mechanical one. As they were when the words name
    no trade, or no work center is that trade's."""
    title = _title(conn, job)
    trade = next((t for t, (words, _) in TRADES.items() if words.search(title)), None) if title else None
    if trade is None:
        return centers
    names = TRADES[trade][1]

    def fits(c: dict) -> bool:
        return bool(names.search(c["description"] or "") or names.search(c["label"] or ""))

    mine = [c for c in centers if fits(c)]
    if mine:
        return mine
    every = conn.execute(
        """
        select e.fuzer_id as id, l.label, gd.value #>> '{}' as description, (gc.value #>> '{}')::numeric as crew
        from entity e left join entity_label l on l.fuzer_id = e.fuzer_id
        left join golden gd on gd.fuzer_id = e.fuzer_id and gd.field = 'description'
        left join golden gc on gc.fuzer_id = e.fuzer_id and gc.field = 'crew_size'
        where e.kind = 'work_center' and e.retired_at is null and e.merged_into is null order by l.label
        """).fetchall()
    return [dict(c) for c in every if fits(c)] or centers


def _title(conn, job: int | None) -> str:
    if job is None:
        return ""
    row = conn.execute("select title from exception_record where id = %s", (job,)).fetchone()
    return (row and row["title"]) or ""


def crew(conn, subjects: list[str], job: int | None = None) -> dict:
    """The crew for a job: each work center that does its work (the one its words' trade needs, when they name one),
    its size and the jobs already sent to it and still open. Ready while every work center has someone free. A full
    crew's blocker is one line with its count; the jobs holding it are on the work center."""
    centers = _trade_centers(conn, _centers(conn, subjects), job)
    if not centers:
        return {"check": "crew", "label": "Crew", "ready": True, "blockers": [], "centers": [], "checked": False,
                "note": "No work orders name a work center for this equipment, so no crew to check."}
    sent = conn.execute(
        """
        select e.id, e.title, e.subjects, w.assignee from exception_record e join work_item w on w.id = e.item_id
        where e.state = 'open' and e.joined_to is null and w.state not in ('done', 'cancelled')
          and e.id is distinct from %s
          and exists (select 1 from event_outbox o where o.topic = 'exception.dispatched' and o.ref = 'exception:' || e.id)
        order by e.id
        """, (job,)).fetchall()
    holding: dict[str, list[dict]] = {c["id"]: [] for c in centers}
    for s in sent:
        for c in _centers(conn, list(s["subjects"] or [])):
            if c["id"] in holding:
                holding[c["id"]].append({"id": s["id"], "title": s["title"], "assignee": s["assignee"]})
    out, blockers = [], []
    for c in centers:
        booked = _booked(conn, c, job)
        if booked is not None:  # a crew on the plan: read the one capacity ledger planned work is booked against
            out.append(booked)
            if booked["full"]:
                name = c["description"] or c["label"]
                blockers.append(f"{name} ({c['label']}): {_n(booked['busy'])} of {_n(booked['crew'])} booked today")
            continue
        jobs = holding[c["id"]]
        size = float(c["crew"]) if c["crew"] is not None else None
        full = size is not None and len(jobs) >= size
        out.append({**c, "crew": size, "busy": len(jobs), "jobs": jobs, "full": full})
        if full:
            name = c["description"] or c["label"]
            blockers.append(f"{name} ({c['label']}): all {_n(size)} busy on {len(jobs)} open job"
                            + ("s" if len(jobs) != 1 else ""))
    return {"check": "crew", "label": "Crew", "ready": not blockers, "blockers": blockers, "centers": out,
            "checked": True, "note": None}


def _booked(conn, center: dict, job: int | None) -> dict | None:
    """A work center's crew read from the plan (step 3 of the work model): the plan resource of kind crew on it, the
    people booked on it today by every source (planned jobs, schedules, work already sent out), less this job's own
    booking, against its capacity today. None when the work center has no crew resource on the plan."""
    from datetime import timedelta

    from .plan import ACTIVE

    res = conn.execute("select * from plan_resource where kind = 'crew' and work_center = %s order by code limit 1",
                       (center["label"],)).fetchone()
    if res is None:
        return None
    start = datetime.combine(clock.today(), datetime.min.time(), tzinfo=clock.zone())
    end = start + timedelta(days=1)
    rows = conn.execute(
        """
        select i.id, i.title, i.exception_id, d.quantity from plan_demand_all d join plan_item i on i.id = d.item_id
        where d.resource = %s and i.status = any(%s) and i.start_at < %s and i.end_at > %s and d.quantity > 0
        order by i.start_at, i.id
        """, (res["code"], list(ACTIVE), end, start)).fetchall()
    others = [r for r in rows if job is None or r["exception_id"] != job]
    window = conn.execute("select min(capacity) as c from plan_resource_capacity where resource = %s and start_at < %s "
                          "and end_at > %s", (res["code"], end, start)).fetchone()
    size = float(window["c"]) if window and window["c"] is not None else float(res["capacity"])
    busy = sum(float(r["quantity"]) for r in others)
    return {**center, "resource": res["code"], "crew": size, "busy": busy, "full": busy + 1 > size,
            "jobs": [{"id": r["exception_id"], "item_id": r["id"], "title": r["title"]} for r in others]}


LIVE = ("issued", "live", "active")
IN_PLACE = ("applied", "verified")


def _locations(conn, subjects: list[str]) -> list[str]:
    """A job's subjects, what is installed at them and where they are installed: the places a permit can cover."""
    if not subjects:
        return []
    return [r["id"] for r in conn.execute(
        "select unnest(%(s)s::text[]) as id union select src from edge where rel = 'installed_at' "
        "and dst = any(%(s)s) and valid_to is null union select dst from edge where rel = 'installed_at' "
        "and src = any(%(s)s) and valid_to is null", {"s": subjects}).fetchall()]


def _needs_permit(conn, job: int | None) -> tuple[bool, str | None]:
    if job is None:
        return False, None
    row = conn.execute("select type from exception_record where id = %s", (job,)).fetchone()
    return bool(row and row["type"] in _permit_kinds(conn)), row["type"] if row else None


_PERMITS = """
    select p.fuzer_id as id, pl.label, c.dst as location, ll.label as location_label,
           max(case when g.field = 'permit_type' then g.value #>> '{}' end) as type,
           max(case when g.field = 'state' then g.value #>> '{}' end) as state,
           max(case when g.field = 'valid_from' then g.value #>> '{}' end) as valid_from,
           max(case when g.field = 'valid_to' then g.value #>> '{}' end) as valid_to,
           max(case when g.field = 'description' then g.value #>> '{}' end) as work,
           (select coalesce(jsonb_agg(jsonb_build_object(
                     'isolation', il.label, 'point', ip.value #>> '{}', 'method', im.value #>> '{}',
                     'state', ist.value #>> '{}') order by il.label), '[]')
            from edge i join entity_label il on il.fuzer_id = i.src
            left join golden ip on ip.fuzer_id = i.src and ip.field = 'point'
            left join golden im on im.fuzer_id = i.src and im.field = 'method'
            left join golden ist on ist.fuzer_id = i.src and ist.field = 'state'
            where i.rel = 'isolation_for' and i.dst = p.fuzer_id) as isolations
    from edge c join entity p on p.fuzer_id = c.src and p.kind = 'permit'
    left join entity_label pl on pl.fuzer_id = p.fuzer_id
    left join entity_label ll on ll.fuzer_id = c.dst
    left join golden g on g.fuzer_id = p.fuzer_id
    where c.rel = 'covers' and c.dst = any(%s)
    group by p.fuzer_id, pl.label, c.dst, ll.label
    order by max(case when g.field = 'valid_to' then g.value #>> '{}' end) desc nulls last, p.fuzer_id, c.dst
"""
_NO_PERMITS = {"check": "permits", "label": "Permit", "ready": True, "blockers": [], "permits": [], "checked": False,
               "needed": False, "note": "No permit-to-work records are loaded, so no permit to check."}


def _no_permits(needed: bool, kind: str | None) -> dict:
    """No permit system loaded (#742, decided 13 Nov 2026): work that needs a permit says so and asks for one in hand,
    recorded on the case; nothing is refused, since there is nothing to check it against."""
    out = {**_NO_PERMITS, "needed": needed}
    if needed:
        out["confirm"] = True
        out["warnings"] = [f"Check permit: this {(kind or 'listed').replace('_', ' ')} work needs a permit, and Fuzer "
                           "has no permit records to check: confirm one is in hand."]
    return out


def _permit_kinds(conn) -> set[str]:
    from ..core import settings

    return {t.strip() for t in str(settings.get(conn, "readiness.permit_types") or "").split(",") if t.strip()}


def _read_permits(rows: list[dict], needed: bool, kind: str | None, now: datetime) -> dict:
    """What the permits covering a job's location say: each with why it is not valid, and the blocker if the job's
    work type needs one and none is."""
    found = []
    for r in rows:
        live = (r["state"] or "").lower() in LIVE
        start, end = _when(r["valid_from"]), _when(r["valid_to"])
        in_date = (start is None or start <= now) and (end is None or now <= end)
        open_isolations = [i for i in r["isolations"] if (i["state"] or "").lower() not in IN_PLACE]
        why = (None if live and in_date and not open_isolations
               else f"{r['state'] or 'no state'}" if not live
               else "expired" if end is not None and now > end
               else "not yet valid" if not in_date
               else "isolations not in place: " + "; ".join(
                   f"{i['isolation']} {i['point'] or ''} ({i['state'] or 'no state'})".replace("  ", " ")
                   for i in open_isolations))
        found.append({**dict(r), "valid": why is None, "why_not": why})
    valid = [p for p in found if p["valid"]]
    blockers = []
    if needed and not valid:
        where = next((p["location_label"] for p in found), None)
        say = "; ".join(f"{p['label']} {p['why_not']}" for p in found[:3])
        blockers.append(f"blocked: no valid permit ({kind} work" + (f" on {where}" if where else "") + "): "
                        + (say if found else "no permit covers this location"))
    note = None if found else "No permit covers this location."
    return {"check": "permits", "label": "Permit", "ready": not blockers, "blockers": blockers, "permits": found,
            "checked": True, "needed": needed, "note": note}


def permits(conn, subjects: list[str], job: int | None = None, now: datetime | None = None) -> dict:
    """Permits to work on a job's location, each with its isolations. Ready unless the job's work type needs a permit
    and none is issued and in date with every isolation in place."""
    needed, kind = _needs_permit(conn, job)
    if not conn.execute("select 1 from entity where kind = 'permit' limit 1").fetchone():
        return _no_permits(needed, kind)
    rows = conn.execute(_PERMITS, (_locations(conn, subjects),)).fetchall()
    return _read_permits(rows, needed, kind, now or datetime.now(UTC))


def locations_many(conn, subjects: dict[int, list[str]]) -> dict[int, set[str]]:
    """`_locations` for many jobs in one query: {job: its subjects, what is installed at them and where they are}."""
    every = sorted({s for ss in subjects.values() for s in ss})
    near: dict[str, set[str]] = {s: {s} for s in every}
    if every:
        for r in conn.execute("select src, dst from edge where rel = 'installed_at' and valid_to is null "
                              "and (src = any(%(s)s) or dst = any(%(s)s))", {"s": every}):
            if r["dst"] in near:
                near[r["dst"]].add(r["src"])
            if r["src"] in near:
                near[r["src"]].add(r["dst"])
    return {job: set().union(*(near[s] for s in ss)) if ss else set() for job, ss in subjects.items()}


def permits_many(conn, jobs: dict[int, tuple[str, list[str]]], places: dict[int, set[str]] | None = None,
                 now: datetime | None = None) -> dict[int, dict]:
    """`permits` for many jobs ({exception id: (its type, its subjects)}) in three queries instead of four each."""
    kinds = _permit_kinds(conn)
    if not conn.execute("select 1 from entity where kind = 'permit' limit 1").fetchone():
        return {job: _no_permits(type in kinds, type) for job, (type, _) in jobs.items()}
    now = now or datetime.now(UTC)
    places = places if places is not None else locations_many(conn, {j: ss for j, (_, ss) in jobs.items()})
    rows = conn.execute(_PERMITS, (sorted(set().union(*places.values())) if places else [],)).fetchall()
    return {job: _read_permits([r for r in rows if r["location"] in places[job]], type in kinds, type, now)
            for job, (type, _) in jobs.items()}


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return clock.aware(t)


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _spares(conn, subjects: list[str], job: int | None = None, plan_item: int | None = None) -> dict:
    return spares(conn, orders(conn, job, plan_item))


def _simops(conn, subjects: list[str], job: int | None = None) -> dict:
    from . import simops

    return simops.at_dispatch(conn, subjects, job)


_MOCS = """
    select m.fuzer_id, m.record, m.quantity, m.value, m.unit, m.at, l.label as asset, r.source_key as moc, r.payload
    from measurement m left join entity_label l on l.fuzer_id = m.fuzer_id
    left join source_record r on m.record = r.source || ':' || r.record_type || ':' || r.source_key
    where m.method = 'moc_open' and m.fuzer_id = any(%s)
    order by m.at desc, m.record
"""


def _read_mocs(rows: list[dict], changes: list[dict] = ()) -> dict:
    found = [{"moc": r["moc"] or r["record"].rsplit(":", 1)[-1], "asset": r["asset"],
              "change": f"{r['quantity'].replace('_', ' ')} {_n(r['value'])} {r['unit'] or ''}".strip(),
              "description": (r["payload"] or {}).get("description"), "since": r["at"].date().isoformat()}
             for r in rows]
    warnings = [f"Open MOC {f['moc']} on {f['asset'] or 'this equipment'}: "
                + (f["description"] or f"proposes {f['change']}").rstrip(".") for f in found]
    from . import safeguards

    for c in changes:  # open change records, with what their stage says (#755)
        st = safeguards.stage(c)
        found.append({"moc": c["label"], "asset": ", ".join(c["about_labels"]) or None, "change": c["change_type"],
                      "description": c["title"], "since": (c["opened"] or "")[:10] or None, "state": c["state"],
                      "work_orders": c["work_orders"], **st})
        warnings.append(safeguards.change_warning(c))
    return {"check": "moc", "label": "MOC", "ready": True, "blockers": [], "checked": True, "mocs": found,
            "warnings": warnings, "note": None if found else "No open MOC on this equipment."}


def mocs(conn, subjects: list[str], job: int | None = None) -> dict:
    """Open management-of-change records on the job's equipment or where it is installed: a warning for whoever sends
    or does the work that a change is proposed or in hand there, never a blocker. Read from whatever MOC records are
    loaded: an open MOC in the register (method `moc_open`), and open change records with what their stage says
    (#755): a temporary change past its end date, or one implemented before its pre-startup safety review. Silent when
    there are none."""
    near = _locations(conn, subjects)
    return _read_mocs(conn.execute(_MOCS, (near,)).fetchall(), _changes_on(conn, set(near)))


def _changes_on(conn, near: set[str], every: list[dict] | None = None) -> list[dict]:
    from . import safeguards

    if not near:
        return []
    return [c for c in (safeguards.changes(conn) if every is None else every) if set(c["about"]) & near]


def mocs_many(conn, places: dict[int, set[str]]) -> dict[int, dict]:
    """`mocs` for many jobs, from their places (`locations_many`), in one query."""
    from . import safeguards

    rows = conn.execute(_MOCS, (sorted(set().union(*places.values())) if places else [],)).fetchall()
    every = safeguards.changes(conn) if places else []
    return {job: _read_mocs([r for r in rows if r["fuzer_id"] in near], _changes_on(conn, near, every))
            for job, near in places.items()}


# What a field report says the job waits on, by the words people use for it. Said only with a word of need beside it
# ("needs a gas test", "no permit", "waiting on scaffold"), so "permit closed" in a note holds nothing.
PREREQUISITES = {
    "a gas test": re.compile(r"(?i)gas[- ]?test"),
    "a permit": re.compile(r"(?i)\bpermit|\bptw\b"),
    "an isolation": re.compile(r"(?i)\bisolat"),
    "depressurising": re.compile(r"(?i)\bde-?pressuri[sz]|\bblow ?down|\bbleed (it )?down"),
    "a scaffold": re.compile(r"(?i)scaffold"),
    "parts": re.compile(r"(?i)\b(parts?|spares?)\b"),
}
NEED = re.compile(r"(?i)\b(needs?|needed|requires?|required|wait(s|ing)?|awaiting|until|no|without|before)\b")


def field(conn, subjects: list[str], job: int | None = None) -> dict:
    """What the field's latest report on the job says it waits on (#1365): a note or could-not-do naming a gas test,
    a permit, an isolation, a scaffold or parts holds the job until a later report. Silent when the field has not
    said."""
    row = conn.execute("select evidence from exception_record where id = %s", (job,)).fetchone() if job else None
    evidence = (row and row["evidence"]) or {}
    reports = (evidence.get("field") if isinstance(evidence, dict) else None) or []
    last = reports[-1] or {} if reports else {}
    note = (last.get("note") or "").strip()
    waits = ([w for w, words in PREREQUISITES.items() if words.search(note)]
             if last.get("status") in ("could_not_do", "note") and NEED.search(note) else [])
    who = last.get("by") or "the field"
    blockers = [f"waits on {_list(waits)}, from {who}'s report: “{note}”"] if waits else []
    return {"check": "field", "label": "Field", "ready": not blockers, "blockers": blockers, "waits_on": waits,
            "checked": bool(reports), "note": None}


def _list(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


CHECKS = (field, _spares, crew, permits, _simops, mocs)


def check(conn, subjects: list[str], job: int | None = None, plan_item: int | None = None) -> dict:
    """Every readiness check for a job on these subjects (`job`, the exception being sent, is not counted against
    itself): ready, or each blocker by name. `unknown` says each check that can't be said and why (parts with no
    stock source), and `undetermined` what would answer it (P6.6)."""
    results = [_spares(conn, subjects, job, plan_item) if c is _spares else c(conn, subjects, job) for c in CHECKS]
    blockers = [b if b.startswith("blocked:") else f"{r['label']}: {b}" for r in results for b in r["blockers"]]
    warnings = [w for r in results for w in r.get("warnings") or []]  # said to whoever sends it, never holding it
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings, "checks": results,
            "unknown": [r["note"] for r in results if r.get("unknown")],
            "undetermined": [n for r in results for n in r.get("undetermined") or []]}


PLANNER = "maintenance_planner"


def holds(conn, to: str) -> bool:
    """Whether readiness holds a send to `to`: never one to the maintenance planner, the queue or a person in it, who
    plans the parts and the crew the check would wait on (#1391)."""
    from ..core import work

    return to != f"role:{PLANNER}" and (to.startswith("role:") or PLANNER not in work.roles_of(conn, to))


def for_exception(conn, exception_id: int) -> dict:
    row = conn.execute("select subjects from exception_record where id = %s", (exception_id,)).fetchone()
    if row is None:
        raise LookupError(f"no exception {exception_id}")
    return check(conn, list(row["subjects"] or []), exception_id)

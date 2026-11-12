"""Ready to dispatch (P5.5, #680): one check before a job is sent. It reads the records the job's equipment has, never
its equipment type:

    spares   the BOM's parts (SAP `uses_part`) against stock, with open purchase orders and their delivery dates
             (P5.5a): a part short is a blocker; one on order names the date it is due
    crew     the work center (SAP `arbpl`) that does the equipment's work orders, its crew size, and the jobs
             already sent to it and still open (P5.5b): a full crew is a blocker naming the jobs holding it

A check with no records to read says so and does not block. A blocked job is sent only with a reason (enforced where
people dispatch: the dispatch route and card), and the reason goes on the case's history.
"""

from datetime import date


def _units(conn, subjects: list[str]) -> list[str]:
    """The equipment behind a job's subjects: each subject and whatever is installed at it now."""
    if not subjects:
        return []
    return [r["id"] for r in conn.execute(
        "select unnest(%(s)s::text[]) as id union select src from edge where rel = 'installed_at' "
        "and dst = any(%(s)s) and valid_to is null", {"s": subjects}).fetchall()]


def spares(conn, subjects: list[str], today: date | None = None) -> dict:
    """The BOM's parts for a job's equipment: needed, in stock, on order. Ready when every part is in stock."""
    today = today or date.today()
    units = _units(conn, subjects)
    rows = conn.execute(
        """
        select b.dst as material, l.label, gd.value #>> '{}' as description,
               max(coalesce((b.props->>'quantity')::numeric, 1)) as needed,
               coalesce((gs.value #>> '{}')::numeric, 0) as stock,
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
        where b.rel = 'uses_part' and b.src = any(%s) and b.valid_to is null
        group by b.dst, l.label, gd.value, gs.value
        order by l.label
        """, (units,)).fetchall()
    parts, blockers = [], []
    for r in rows:
        needed, stock = float(r["needed"]), float(r["stock"])
        short = max(0.0, needed - stock)
        coming = [o for o in r["orders"] if o["due"]]
        part = {"material": r["material"], "label": r["label"], "description": r["description"], "needed": needed,
                "stock": stock, "short": short, "orders": coming}
        parts.append(part)
        if short:
            name = r["description"] or r["label"] or r["material"]
            due = next((o for o in coming if (o["quantity"] or 0) >= short), coming[0] if coming else None)
            blockers.append(f"{name}: {_n(stock)} in stock, {_n(needed)} needed; "
                            + (f"on order ({due['po']}), due {due['due']}" if due else "nothing on order"))
    return {"check": "spares", "label": "Spares", "ready": not blockers, "blockers": blockers, "parts": parts,
            "checked": bool(rows), "note": None if rows else "No BOM for this equipment, so no parts to check."}


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


def crew(conn, subjects: list[str], job: int | None = None) -> dict:
    """The crew for a job: each work center that does its work, its size and the jobs already sent to it and still
    open. Ready while every work center has someone free."""
    centers = _centers(conn, subjects)
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
        jobs = holding[c["id"]]
        size = float(c["crew"]) if c["crew"] is not None else None
        full = size is not None and len(jobs) >= size
        out.append({**c, "crew": size, "busy": len(jobs), "jobs": jobs, "full": full})
        if full:
            name = c["description"] or c["label"]
            held = "; ".join(f"#{j['id']} {j['title']}" for j in jobs[:5]) + ("; ..." if len(jobs) > 5 else "")
            blockers.append(f"{name} ({c['label']}): all {_n(size)} busy on open jobs: {held}")
    return {"check": "crew", "label": "Crew", "ready": not blockers, "blockers": blockers, "centers": out,
            "checked": True, "note": None}


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def _spares(conn, subjects: list[str], job: int | None = None) -> dict:
    return spares(conn, subjects)


CHECKS = (_spares, crew)


def check(conn, subjects: list[str], job: int | None = None) -> dict:
    """Every readiness check for a job on these subjects (`job`, the exception being sent, is not counted against
    itself): ready, or each blocker by name."""
    results = [c(conn, subjects, job) for c in CHECKS]
    blockers = [f"{r['label']}: {b}" for r in results for b in r["blockers"]]
    return {"ready": not blockers, "blockers": blockers, "checks": results}


def for_exception(conn, exception_id: int) -> dict:
    row = conn.execute("select subjects from exception_record where id = %s", (exception_id,)).fetchone()
    if row is None:
        raise LookupError(f"no exception {exception_id}")
    return check(conn, list(row["subjects"] or []), exception_id)

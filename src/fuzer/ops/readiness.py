"""Ready to dispatch (P5.5, #680): one check before a job is sent. It reads the records the job's equipment has, never
its equipment type:

    spares   the BOM's parts (SAP `uses_part`) against stock, with open purchase orders and their delivery dates
             (P5.5a): a part short is a blocker; one on order names the date it is due

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


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


CHECKS = (spares,)


def check(conn, subjects: list[str]) -> dict:
    """Every readiness check for a job on these subjects: ready, or each blocker by name."""
    results = [c(conn, subjects) for c in CHECKS]
    blockers = [f"{r['label']}: {b}" for r in results for b in r["blockers"]]
    return {"ready": not blockers, "blockers": blockers, "checks": results}


def for_exception(conn, exception_id: int) -> dict:
    row = conn.execute("select subjects from exception_record where id = %s", (exception_id,)).fetchone()
    if row is None:
        raise LookupError(f"no exception {exception_id}")
    return check(conn, list(row["subjects"] or []))

"""Material readiness (#362): whether the parts reserved for open work orders will be there on the day each job needs
them, from the records alone: stock on hand, open purchase orders with their delivery dates, and each material's
planned delivery time.

    coverage(conn, as_of, ref)   every open reservation (or those on one work order or one material): covered from
                                 stock, covered by an order due in time, late (an order due after the need date, by
                                 how many days), or not on order (and whether the planned delivery time still fits)

Stock and orders are allocated to reservations in need-date order, earliest job first, one material at a time, so two
jobs can't both count the same part. A purchase order's delivery date is what the record says, not a promise: an
arrival is never stated beyond it, and nothing here says a part is ordered unless an open order is on record.
Reserving, ordering and expediting stay with supply chain.
"""

from datetime import date, timedelta

from ..core import clock
from .calcs import CalcError

OPEN_ORDERS = ("CRTD", "REL", "PCNF")  # SAP order statuses still to be done
VERDICT_ORDER = ("late", "not_on_order_too_late", "not_on_order", "on_order", "in_stock")


def _rows(conn) -> list[dict]:
    return conn.execute(
        """
        select r.src as work_order, wl.label as order_label, r.dst as material, ml.label as material_label,
               md.value #>> '{}' as description, (ms.value #>> '{}')::numeric as stock,
               (mt.value #>> '{}')::numeric as lead_time_days, coalesce((r.props->>'quantity')::numeric, 1) as quantity,
               r.props->>'need_date' as need_date, r.props->>'reservation' as reservation,
               ws.value #>> '{}' as order_status, wt.value #>> '{}' as order_text
        from edge r
        join golden ws on ws.fuzer_id = r.src and ws.field = 'status'
        left join golden wt on wt.fuzer_id = r.src and wt.field = 'text'
        left join entity_label wl on wl.fuzer_id = r.src
        left join entity_label ml on ml.fuzer_id = r.dst
        left join golden md on md.fuzer_id = r.dst and md.field = 'description'
        left join golden ms on ms.fuzer_id = r.dst and ms.field = 'stock'
        left join golden mt on mt.fuzer_id = r.dst and mt.field = 'lead_time_days'
        where r.rel = 'reserves' and r.valid_to is null and coalesce(r.props->>'status', 'open') = 'open'
          and ws.value #>> '{}' = any(%s)
        """, (list(OPEN_ORDERS),)).fetchall()


def _orders(conn, materials: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
            """
            select o.dst as material, pl.label as po, coalesce((pq.value #>> '{}')::numeric, 0) as quantity,
                   pd.value #>> '{}' as due, pv.value #>> '{}' as vendor
            from edge o join entity po on po.fuzer_id = o.src and po.kind = 'purchase_order'
            left join entity_label pl on pl.fuzer_id = po.fuzer_id
            left join golden pq on pq.fuzer_id = po.fuzer_id and pq.field = 'quantity'
            left join golden pd on pd.fuzer_id = po.fuzer_id and pd.field = 'delivery_date'
            left join golden pv on pv.fuzer_id = po.fuzer_id and pv.field = 'vendor'
            left join golden ps on ps.fuzer_id = po.fuzer_id and ps.field = 'status'
            where o.rel = 'orders' and o.dst = any(%s) and coalesce(ps.value #>> '{}', 'open') = 'open'
            order by pd.value #>> '{}' nulls last
            """, (materials,)).fetchall():
        out.setdefault(r["material"], []).append(dict(r) | {"quantity": float(r["quantity"])})
    return out


def coverage(conn, as_of: str | None = None, ref: str | None = None) -> dict:
    """Each open reservation against stock and open purchase orders, allocated earliest need first; late and
    not-on-order lines first."""
    today = date.fromisoformat(as_of[:10]) if as_of else clock.today()
    rows = _rows(conn)
    if ref:
        want = ref.upper()
        rows = [r for r in rows if want in {(r["order_label"] or "").upper(), (r["material_label"] or "").upper(),
                                            r["work_order"].upper(), r["material"].upper()}]
        if not rows:
            raise CalcError("no open reservation on that work order or material")
    if not rows:
        return {"as_of": today.isoformat(), "lines": [], "verdict": "no_reservations", "summary": {"lines": 0},
                "note": "No open reservations on record: the reservations export isn't loaded, or no open order "
                        "reserves a part. Stock on hand alone doesn't say what a job will have."}
    pos = _orders(conn, sorted({r["material"] for r in rows}))
    lines = []
    for mat in sorted({r["material"] for r in rows}):
        mine = sorted((r for r in rows if r["material"] == mat), key=lambda r: (r["need_date"] or "9999", r["order_label"] or ""))
        pool = float(mine[0]["stock"] or 0)
        orders = [dict(o, left=o["quantity"]) for o in pos.get(mat, [])]
        for r in mine:
            qty, need_on = float(r["quantity"]), r["need_date"]
            line = {"work_order": r["order_label"], "work_order_id": r["work_order"], "order_text": r["order_text"],
                    "material": r["material_label"], "material_id": mat, "description": r["description"],
                    "reservation": r["reservation"], "quantity": qty, "need_date": need_on, "stock": float(r["stock"] or 0),
                    "lead_time_days": float(r["lead_time_days"]) if r["lead_time_days"] is not None else None}
            take = min(pool, qty)
            pool -= take
            still = qty - take
            covering = []
            for o in orders:
                if still <= 0:
                    break
                if o["left"] > 0:
                    got = min(o["left"], still)
                    o["left"] -= got
                    still -= got
                    covering.append({"po": o["po"], "quantity": got, "due": o["due"], "vendor": o["vendor"]})
            line["from_stock"], line["on_order"] = take, covering
            due = max((c["due"] for c in covering if c["due"]), default=None)
            if take >= qty:
                line["verdict"], line["why"] = "in_stock", f"{_n(take)} from stock"
            elif still <= 0 and due and need_on and due <= need_on:
                line["verdict"] = "on_order"
                line["why"] = f"{_n(take)} from stock, the rest on order, due {due} for qty {need_on}"
            elif still <= 0 and due and need_on:
                late = (date.fromisoformat(due[:10]) - date.fromisoformat(need_on[:10])).days
                line["verdict"], line["days_late"] = "late", late
                po = ", ".join(c["po"] for c in covering)
                line["why"] = f"on order ({po}), due {due}: {late} days after the job needs it on {need_on}"
            else:
                lead = line["lead_time_days"]
                earliest = (today + timedelta(days=lead)).isoformat() if lead is not None else None
                line["short"] = still
                line["earliest_if_ordered_now"] = earliest
                too_late = bool(earliest and need_on and earliest > need_on)
                line["verdict"] = "not_on_order_too_late" if too_late else "not_on_order"
                line["why"] = (f"{_n(still)} short with nothing on order; "
                               + (f"a planned delivery time of {_n(lead)} days puts an order placed today at {earliest}"
                                  + (f", after the qty date {need_on}" if too_late else f", before the qty date {need_on}")
                                  if earliest else "no planned delivery time on the material to say when one could come"))
            lines.append(line)
    lines.sort(key=lambda x: (VERDICT_ORDER.index(x["verdict"]), x["need_date"] or "", x["work_order"] or ""))
    counts = {v: sum(1 for x in lines if x["verdict"] == v) for v in VERDICT_ORDER}
    flagged = [x for x in lines if x["verdict"] in ("late", "not_on_order_too_late")]
    return {"as_of": today.isoformat(), "lines": lines, "summary": {"lines": len(lines), **counts,
                                                                    "flagged": [f"{x['work_order']} {x['material']}"
                                                                                for x in flagged]},
            "verdict": "late_parts" if flagged else "short_not_on_order" if counts["not_on_order"] else "covered",
            "source": "SAP reservations, stock on hand, open purchase orders and planned delivery times",
            "note": ("Stock and open orders are allocated to reservations earliest need first. A delivery date is the "
                     "purchase order's own; nothing here says a part is ordered, reserved or expedited beyond the "
                     "record. Ordering and expediting stay with supply chain.")}


def _n(x) -> str:
    return f"{float(x):g}"

"""Work that the materials records bring (P3.13, #1180): a planned job whose parts won't be there on the day, raised as
routed work for the maintenance planner before a crew goes out for nothing.

`analysis.material_readiness.coverage` allocates stock and open purchase orders to each open reservation, earliest
need first. A reservation whose order lands after the job needs it (`late`), or that is short with nothing on order
and a planned delivery time that can't make the need date (`not_on_order_too_late`), is a part the job won't have.
The `parts_not_ready` detector raises one `parts_not_ready` case per work order with every such line on it, keyed by
the order, with the order and the equipment it concerns as subjects. It clears when every line on the order is covered
by stock or an order due in time. A part short with time still to order it is not yet work. Ordering and expediting
stay with supply chain; a delivery date is what the purchase order says, not a promise.
"""

from datetime import date, datetime

from ..analysis import material_readiness
from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

NOT_READY = ("late", "not_on_order_too_late")


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("parts_not_ready") or exception_type(
        "parts_not_ready", "Parts not ready for planned work", role="role:maintenance_planner", audience=("ioc",),
        joins=False,
        description="A planned work order with a reserved part that won't arrive by the day the job needs it: on "
                    "order too late, or short with nothing on order and too little time to get it. Expedite the "
                    "order, find the part elsewhere, or move the job, before a crew goes out without it. It clears "
                    "when every part is covered by stock or an order due in time.")


TYPE = _type().name


def _day(now) -> str | None:
    if now is None:
        return None
    if isinstance(now, datetime):
        now = now.date()
    return now.isoformat() if isinstance(now, date) else str(now)[:10]


def not_ready(conn, now=None) -> list[dict]:
    """Per work order, the reserved parts it won't have on the day, as of `now` (else today)."""
    out = material_readiness.coverage(conn, _day(now))
    orders: dict[str, dict] = {}
    for line in out["lines"]:
        if line["verdict"] not in NOT_READY:
            continue
        o = orders.setdefault(line["work_order_id"], {"id": line["work_order_id"], "work_order": line["work_order"],
                                                      "text": line["order_text"], "lines": [], "as_of": out["as_of"]})
        o["lines"].append(line)
    for o in orders.values():
        o["need_date"] = min((x["need_date"] for x in o["lines"] if x["need_date"]), default=None)
        o["concerns"] = [r["dst"] for r in conn.execute(
            "select dst from edge where src = %s and rel = 'concerns' and valid_to is null order by dst", (o["id"],))]
    return sorted(orders.values(), key=lambda o: (o["need_date"] or "9999", o["work_order"] or ""))


def _reservations(conn, pack) -> bool:
    return conn.execute("select 1 from edge where rel = 'reserves' limit 1").fetchone() is not None


@detectors.register("parts_not_ready", title="A planned job whose reserved parts won't be there on the day",
                    kind=detectors.RULE, cadence="daily", raises=(TYPE,),
                    needs={"SAP reservations": _reservations},
                    eval="test:tests/ops/test_parts_work.py::test_a_job_whose_parts_wont_arrive_is_work_until_covered",
                    found=lambda out: len(out["raised"]))
def watch_parts(conn, by: str = "agent:materials", now=None) -> dict:
    """Each open work order with a reserved part on order too late, or short with nothing on order and no time left
    to get it, raised as `parts_not_ready` for the maintenance planner (keyed by the order); open ones now covered
    are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (TYPE,))]
    orders = not_ready(conn, now)
    raised, cleared, errors = [], [], []
    for o in orders:
        items = [fact("Work order", f"{o['work_order']}: {o['text'] or ''}".rstrip(": ")),
                 at("Earliest need date", o["need_date"]) if o["need_date"] else fact("Earliest need date", "none")]
        for x in o["lines"]:
            items.append(fact(f"{x['material']} {x['description'] or ''}".strip(), x["why"]))
        found = Finding(
            "materials.parts_not_ready", DETERMINISTIC,
            method="SAP reservations against stock on hand, open purchase orders and planned delivery times, "
                   "allocated earliest need first",
            start=o["as_of"], data={"work_order": o["work_order"], "lines": [
                {"material": x["material"], "verdict": x["verdict"], "need_date": x["need_date"],
                 "days_late": x.get("days_late"), "earliest_if_ordered_now": x.get("earliest_if_ordered_now")}
                for x in o["lines"]]}, items=tuple(items))
        late = [x for x in o["lines"] if x["verdict"] == "late"]
        none = [x for x in o["lines"] if x["verdict"] == "not_on_order_too_late"]
        bits = ([f"{len(late)} part{'s' if len(late) > 1 else ''} on order too late"] if late else []) + \
               ([f"{len(none)} part{'s' if len(none) > 1 else ''} not on order in time"] if none else [])
        title = f"{o['work_order']}: " + " and ".join(bits) + (f" for the job on {o['need_date']}" if o["need_date"] else "")
        try:
            x = raise_exception(conn, TYPE, o["work_order"], title, by, source="sap", refs=[o["id"], *o["concerns"]],
                                priority=0.6, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{o['work_order']}: {e}")
            continue
        raised.append({"work_order": o["work_order"], "lines": len(o["lines"]), "exception_id": x["id"],
                       "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {o["work_order"] for o in orders}:
        clear_exception(conn, TYPE, key, by, reason="every reserved part is covered by stock or an order due in time")
        cleared.append(key)
    conn.commit()
    return {"orders": len(orders), "raised": raised, "cleared": cleared, "errors": errors}

"""Backlog health for the work management specialist (#474): how old the open work is, what urgent work is late, and
the backlog ranked by risk rather than by age.

    health(conn, pack, as_of)   {ageing, overdue_by_priority, flags, ranked, basis}

Flags, each with its threshold:
- `ageing`: more than 25 % of open corrective orders (PM01) are older than 90 days (a backlog that isn't being
  worked down, not just a big one);
- `urgent_overdue`: priority 1 or 2 orders past their basic finish;
- `unplanned_risk`: open notifications with no order on units that are bad actors or caused deferment.

`ranked` is the shared value / risk score (analysis/value.py) for open work orders: production at risk, risk matrix
from criticality and failure history, compliance, urgency and effort, so "what first" is argued in money.
"""

from datetime import date
from statistics import median

from ..graph import operations
from . import value

AGEING_DAYS = 90
AGEING_SHARE = 0.25


def health(conn, pack, as_of_date: str | None = None, limit: int = 10) -> dict:
    b = operations.work_order_backlog(conn, as_of_date, limit=1000)
    every = conn.execute(
        """
        select (g.value #>> '{}') as start, (t.value #>> '{}') as type
        from entity e join golden s on s.fuzer_id = e.fuzer_id and s.field = 'status'
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'start'
        left join golden t on t.fuzer_id = e.fuzer_id and t.field = 'order_type'
        where e.kind = 'work_order' and e.merged_into is null and s.value #>> '{}' = any(%s)
        """, (list(operations.OPEN),)).fetchall()
    today = date.fromisoformat(b["as_of"])
    ages = [(today - date.fromisoformat(r["start"][:10])).days for r in every
            if r["start"] and (r["type"] or "PM01") == "PM01" and r["start"][:10] <= b["as_of"]]
    old = sum(1 for a in ages if a > AGEING_DAYS)
    share = old / len(ages) if ages else 0.0
    by_priority: dict[str, int] = {}
    for o in b["overdue_items"]:
        by_priority[str(o["priority"])] = by_priority.get(str(o["priority"]), 0) + 1
    urgent = sum(n for p, n in by_priority.items() if p in ("1", "2"))
    bad = {a["id"] for a in operations.bad_actors(conn, limit=50)["bad_actors"]}
    unplanned_risky = [n for n in b["notifications_without_order"] if n.get("tag_id") in bad]
    flags = [f for f, on in (("ageing", share > AGEING_SHARE), ("urgent_overdue", urgent > 0),
                             ("unplanned_risk", bool(unplanned_risky))) if on]
    r = value.ranked(conn, pack, ("work_order",), b["as_of"], "value", None, limit)
    return {"as_of": b["as_of"], "open_orders": b["open_orders"],
            "ageing": {"corrective_open": len(ages), "median_age_days": median(ages) if ages else None,
                       "older_than_90_days": old, "share_older_than_90_days": round(share, 3)},
            "overdue_by_priority": dict(sorted(by_priority.items())), "urgent_overdue": urgent,
            "unplanned_on_bad_actors": unplanned_risky[:limit], "flags": flags,
            "ranked": [{k: s.get(k) for k in ("id", "label", "tag", "value_usd", "value_per_effort", "reason")}
                       for s in r["items"]], "currency": r["currency"],
            "basis": f"SAP work orders and notifications as of {b['as_of']}; ageing: corrective (PM01) orders older "
                     f"than {AGEING_DAYS} days, flagged above {int(AGEING_SHARE * 100)} %; urgent: priority 1-2 past "
                     "basic finish; ranked by the shared value / risk score over the pack's horizon"}

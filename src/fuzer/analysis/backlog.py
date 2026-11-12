"""Backlog health for the work management specialist (#474): how old the open work is, what urgent work is late, and
the backlog ranked by risk rather than by age.

    health(conn, pack, as_of)   {ageing, overdue_by_priority, flags, ranked, basis}
    burn(conn, since, until)    per month: orders created against closed and the open backlog at its end, and
                                the lag from notification to order (#348)

Flags, each with its threshold:
- `ageing`: more than 25 % of open corrective orders (PM01) are older than 90 days (a backlog that isn't being
  worked down, not just a big one);
- `urgent_overdue`: priority 1 or 2 orders past their basic finish;
- `unplanned_risk`: open notifications with no order on units that are bad actors or caused deferment.

`ranked` is the shared value / risk score (analysis/value.py) for open work orders: production at risk, risk matrix
from criticality and failure history, compliance, urgency and effort, so "what first" is argued in money.
"""

from datetime import date, timedelta
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


def _month_ends(since: str, until: str) -> list[tuple[str, str]]:
    """[start, next start) for each calendar month touching [since, until), clipped to the window."""
    out, d = [], date.fromisoformat(since[:10])
    end = date.fromisoformat(until[:10])
    while d < end:
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((d.isoformat(), min(nxt, end).isoformat()))
        d = nxt
    return out


def _open_at(orders: list[dict], day: str) -> tuple[list[dict], int]:
    """Orders open at the start of `day`, and how many closed ones have no finish date to place them by."""
    open_, unplaced = [], 0
    for o in orders:
        f = o["f"]
        began, finish = str(f.get("start") or "")[:10], str(f.get("finish") or "")[:10]
        if not began or began >= day:
            continue
        if finish:
            if finish >= day:
                open_.append(o)
        elif f.get("status") in operations.OPEN:
            open_.append(o)
        else:
            unplaced += 1
    return open_, unplaced


def burn(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Is the backlog under control (#348)? For each month in [since, until) (default the `days` to the latest daily
    report): work orders created (by start) against closed (by finish), and the orders open at the month's end. Then
    the days from a notification's creation to the order raised for it; orders with no notification are counted and
    left out of the lag, never read as zero."""
    given = since
    since, until = operations._window(conn, since, until, days)
    if not given and since[8:10] != "01":  # whole months: start at the first month wholly in the window
        since = (date.fromisoformat(since[:8] + "01") + timedelta(days=32)).replace(day=1).isoformat()
    orders = operations._rows(conn, "work_order")
    note = {r["src"]: r["dst"] for r in conn.execute(
        "select src, dst from edge where rel = 'for_notification' and src = any(%s)", ([o["id"] for o in orders],))}
    created_on = {n["id"]: str(n["f"].get("created") or "")[:10] for n in operations._rows(conn, "notification")}
    months = []
    for start, nxt in _month_ends(since, until):
        made = [o for o in orders if start <= str(o["f"].get("start") or "")[:10] < nxt]
        shut = [o for o in orders if start <= str(o["f"].get("finish") or "")[:10] < nxt]
        open_, unplaced = _open_at(orders, nxt)
        by_type: dict[str, dict] = {}
        for key, rows in (("created", made), ("closed", shut), ("open_at_end", open_)):
            for o in rows:
                t = o["f"].get("order_type") or "unknown"
                by_type.setdefault(t, {"created": 0, "closed": 0, "open_at_end": 0})[key] += 1
        months.append({"month": start[:7], "since": start, "until": nxt, "created": len(made), "closed": len(shut),
                       "net": len(made) - len(shut), "open_at_end": len(open_), "closed_without_finish": unplaced,
                       "by_type": dict(sorted(by_type.items())),
                       "refs": {"created": [o["id"] for o in made][:50], "closed": [o["id"] for o in shut][:50]}})
    lags, no_note, before = [], [], []
    for o in orders:
        began = str(o["f"].get("start") or "")[:10]
        if not (since <= began < until):
            continue
        n = note.get(o["id"])
        made_n = created_on.get(n) if n else None
        if not made_n:
            no_note.append(o["id"])
            continue
        lag = (date.fromisoformat(began) - date.fromisoformat(made_n)).days
        item = {"order": o["id"], "label": o["label"], "notification": n, "notified": made_n, "ordered": began,
                "lag_days": lag, "type": o["f"].get("order_type"), "priority": o["f"].get("priority")}
        (before if lag < 0 else lags).append(item)
    days_ = sorted(x["lag_days"] for x in lags)
    in_window = sum(1 for o in orders if since <= str(o["f"].get("start") or "")[:10] < until)

    def pct(q: float) -> int | None:
        return days_[min(len(days_) - 1, int(q * len(days_)))] if days_ else None

    first, last = len(_open_at(orders, since)[0]), months[-1]["open_at_end"] if months else 0
    trend = "growing" if last > first * 1.1 else "shrinking" if last < first * 0.9 else "steady"  # 10 % either way
    return {"since": since, "until": until, "source": "SAP work orders (start, finish, status) and the notifications "
            "they were raised for", "months": months,
            "summary": {"created": sum(m["created"] for m in months), "closed": sum(m["closed"] for m in months),
                        "open_at_start": len(_open_at(orders, since)[0]),
                        "open_at_end": months[-1]["open_at_end"] if months else None, "trend": trend},
            "lag": {"orders": in_window, "with_notification": len(lags) + len(before),
                    "without_notification": len(no_note), "ordered_before_notified": len(before),
                    "median_days": median(days_) if days_ else None, "p90_days": pct(0.9),
                    "max_days": days_[-1] if days_ else None,
                    "longest": sorted(lags, key=lambda x: -x["lag_days"])[:10],
                    "before": before[:10], "without_refs": no_note[:50]},
            "note": "Created is by order start and closed by finish date; an order closed with no finish date is "
                    "counted apart, not placed. The lag covers only orders raised for a notification: the rest are "
                    "counted and left out, never read as zero. Findings inform the people who set priorities."}

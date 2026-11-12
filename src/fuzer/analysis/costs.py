"""Maintenance spend and the cost of unreliability (P7.3c, #335 and #352): what work orders cost by unit, class and
order type, and what each unit's unreliability costs in lost oil and corrective work, ranked for a trade-off
conversation between production and maintenance.

    spend(conn, since, until, by)          work order cost by unit, ISO 14224 class or order type, in each currency,
                                           with the orders that carry no cost counted
    unreliability(conn, pack, since, until)   per unit: oil lost to the downtime it caused at the pack's price, plus
                                           its corrective orders' cost, ranked

Money is never summed across currencies. A work order's currency comes from the source (`currency`, SAP's WAERS);
when the source states none it is unknown, and an unknown currency is its own bucket. The lost oil is valued in the
pack's currency at the pack's price, so a unit's total adds its orders only when they are in that same currency;
otherwise the parts stand side by side and the total is left unsaid.

These are operational estimates from recorded cost, not financial figures: no accounting reconciliation is implied.
"""

from collections import defaultdict
from datetime import date, timedelta

from ..graph import operations as ops
from ..graph.queries import _DOWNTIME
from . import value

UNKNOWN = "unknown"
BY = ("unit", "class", "type")


def _window(conn, since: str | None, until: str | None, days: int) -> tuple[str, str]:
    until = until or (date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)).isoformat()
    return since or (date.fromisoformat(until) - timedelta(days=days)).isoformat(), until


def _orders(conn, since: str, until: str, types: tuple[str, ...] | None = None) -> list[dict]:
    """Work orders started in [since, until), with their cost (None when the source gives none) and currency."""
    rows = ops._rows(conn, "work_order", "f->>'start' >= %(s)s and f->>'start' < %(u)s", {"s": since, "u": until})
    out = []
    for o in rows:
        f = o["f"]
        if types and f.get("order_type") not in types:
            continue
        raw = f.get("cost")
        try:
            cost = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            cost = None
        out.append({"id": o["id"], "order": o["label"], "type": f.get("order_type") or UNKNOWN, "cost": cost,
                    "currency": (str(f.get("currency") or "").strip().upper() or UNKNOWN), "start": f.get("start")})
    tags = ops._tags_of(conn, [o["id"] for o in out])
    classes = {r["src"]: (r["dst"], r["label"]) for r in conn.execute(
        "select c.src, c.dst, coalesce(l.label, c.dst) as label from edge c left join entity_label l on "
        "l.fuzer_id = c.dst where c.rel = 'classified_as' and c.src = any(%s)",
        ([t["id"] for t in tags.values()],))}
    for o in out:
        t = tags.get(o["id"])
        o["unit"] = (t["id"], t["label"], t.get("description")) if t else None
        o["class"] = classes.get(t["id"]) if t else None
    return out


def spend(conn, since: str | None = None, until: str | None = None, by: str = "unit", days: int = 365,
          limit: int = 25) -> dict:
    """Work order cost in [since, until) (default the `days` to the latest daily report) by unit, class or order type,
    one row per group and currency, ranked by cost within each currency. Orders without a cost are counted, and an
    order on no unit (or a unit with no class) is grouped as such, not dropped."""
    if by not in BY:
        raise ValueError(f"spend is by {', '.join(BY)}")
    since, until = _window(conn, since, until, days)
    orders = _orders(conn, since, until)
    groups: dict[tuple[str, str], dict] = {}
    totals: dict[str, float] = defaultdict(float)
    for o in orders:
        if by == "unit":
            key, label, desc = o["unit"] or ("", "no unit", None)
        elif by == "class":
            key, label = o["class"] or ("", "no class")
            desc = None
        else:
            key, label, desc = o["type"], o["type"], None
        g = groups.setdefault((key, o["currency"]), {"id": key or None, "label": label, "description": desc,
                                                     "currency": o["currency"], "cost": 0.0, "orders": 0,
                                                     "orders_without_cost": 0, "refs": []})
        g["orders"] += 1
        g["refs"].append(o["id"])
        if o["cost"] is None:
            g["orders_without_cost"] += 1
        else:
            g["cost"] += o["cost"]
            totals[o["currency"]] += o["cost"]
    rows = sorted(groups.values(), key=lambda g: (g["currency"] == UNKNOWN, g["currency"], -g["cost"], g["label"]))
    for g in rows:
        g["cost"] = round(g["cost"])
        g["share_of_currency_pct"] = round(100 * g["cost"] / totals[g["currency"]], 1) if totals[g["currency"]] else None
        g["refs"] = g["refs"][:50]
    without = sum(1 for o in orders if o["cost"] is None)
    currencies = sorted({o["currency"] for o in orders}, key=lambda c: (c == UNKNOWN, c))
    return {"since": since, "until": until, "by": by, "source": "work orders (cost and, where given, currency)",
            "orders": len(orders), "orders_without_cost": without,
            "without_cost_pct": round(100 * without / len(orders), 1) if orders else None,
            "currencies": currencies, "totals": {c: round(totals[c]) for c in currencies},
            "mixed_currencies": len(currencies) > 1, "rows": rows[:limit] if limit else rows,
            "note": "Recorded maintenance cost, not accounting truth; each currency is totalled apart and an unknown "
                    "currency is never summed with a known one."}


def _in(costs: dict) -> str:
    known = sorted(c for c in costs if c != UNKNOWN)
    parts = ([f"is in {', '.join(known)}"] if known else []) + (["has no currency in the source"] if UNKNOWN in costs else [])
    return " and ".join(parts)


def unreliability(conn, pack, since: str | None = None, until: str | None = None, days: int = 365,
                  limit: int = 20) -> dict:
    """Per unit, the cost of its unreliability in [since, until): oil lost to the downtime it caused (caused_by) at the
    pack's oil price, and its corrective (PM01) orders' cost in their own currency. Ranked by the total where one can
    be said, else by the lost oil's value; the price and both currencies go with every row."""
    since, until = _window(conn, since, until, days)
    cfg = value.config(pack)
    price, cur = float(cfg["prices"]["oil_sm3"]), cfg["currency"]
    units: dict[str, dict] = {}

    def unit(uid: str, label: str | None, desc: str | None = None) -> dict:
        return units.setdefault(uid, {"id": uid, "unit": label or uid, "description": desc, "lost_oil_sm3": 0.0,
                                      "downtime": [], "orders": [], "order_cost": defaultdict(float),
                                      "orders_without_cost": 0})
    for r in conn.execute(
        _DOWNTIME + "select dd.id, dd.f, c.dst as unit, coalesce(l.label, c.dst) as label from dd join edge c on "
        "c.src = dd.id and c.rel = 'caused_by' left join entity_label l on l.fuzer_id = c.dst",
            {"id": None, "since": since, "until": until}):
        u = unit(r["unit"], r["label"])
        u["lost_oil_sm3"] += ops._num(r["f"].get("lost_oil_sm3"))
        u["downtime"].append(r["id"])
    for o in _orders(conn, since, until, ("PM01",)):
        if not o["unit"]:
            continue
        u = unit(*o["unit"])
        u["description"] = u["description"] or o["unit"][2]
        u["orders"].append(o["id"])
        if o["cost"] is None:
            u["orders_without_cost"] += 1
        else:
            u["order_cost"][o["currency"]] += o["cost"]
    desc = {r["fuzer_id"]: r["d"] for r in conn.execute(
        "select fuzer_id, value #>> '{}' as d from golden where field = 'description' and fuzer_id = any(%s)",
        (list(units),))}
    rows = []
    for u in units.values():
        u["description"] = u["description"] or desc.get(u["id"])
        lost_value = round(u["lost_oil_sm3"] * price)
        costs = {c: round(v) for c, v in u["order_cost"].items()}
        addable = set(costs) <= {cur}
        rows.append({"id": u["id"], "unit": u["unit"], "description": u["description"],
                     "lost_oil_sm3": round(u["lost_oil_sm3"], 1), "lost_oil_value": lost_value,
                     "order_cost": costs, "orders": len(u["orders"]), "orders_without_cost": u["orders_without_cost"],
                     "total": lost_value + sum(costs.values()) if addable else None,
                     "total_currency": cur if addable else None,
                     "why_no_total": None if addable else
                     f"its order cost {_in(costs)}, not {cur}, so the parts aren't added",
                     "refs": (u["downtime"] + u["orders"])[:50]})
    rows.sort(key=lambda r: (-(r["total"] if r["total"] is not None else r["lost_oil_value"]), r["unit"]))
    order_currencies = sorted({c for r in rows for c in r["order_cost"]}, key=lambda c: (c == UNKNOWN, c))
    return {"since": since, "until": until, "price": {"oil_sm3": price, "currency": cur, "source": cfg["source"]},
            "order_currencies": order_currencies, "units": rows[:limit] if limit else rows, "units_total": len(rows),
            "source": "downtime records (caused by a unit) and corrective work orders (PM01)",
            "note": f"An operational estimate, not a financial figure: lost oil at {price:g} {cur} per Sm3 "
                    f"({cfg['source']}) plus recorded order cost. A unit's total is said only when its order cost is "
                    f"in {cur}; an unknown currency is never added."}

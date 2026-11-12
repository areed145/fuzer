"""Operational against accounting volumes, and unit cost (P7.5c, #372): the daily allocated figures set beside what
production accounting closed for the month, and the ledger's operating cost per accounted Sm3 of oil.

    reconcile(conn, since, until)   per month and product: the sum of the daily reports (EC DAY001) over the
                                    accounting period against the accounted volume, with both sources, both periods
                                    and the records each rests on
    unit_cost(conn, since, until)   per month and currency: booked operating cost over accounted oil

A month reads

    agrees          within 1 % of the accounted volume
    differs         further apart than that: for production accounting to resolve, with the comment it booked
    preliminary     the month isn't closed in accounting yet; the gap is shown and nothing is concluded
    incomplete      the daily reports don't cover every day of the accounting period, so their sum isn't comparable
    no_accounting   daily reports, but no accounted volume for the month
    no_operational  an accounted volume with no daily report in its period

Nothing is restated: the accounted volume is the authority and the difference is for accountants to resolve. Cost is
never summed across currencies, and a month without a closed oil volume has no unit cost (unknown, never zero).
"""

from collections import defaultdict
from datetime import date, timedelta

from ..graph import operations as ops

TOLERANCE_PCT = 1.0  # a month within this of the accounted volume agrees
PRODUCTS = {"oil": ("oil_sm3", "Sm3"), "gas": ("gas_ksm3", "ksm3")}
REFS = 50
OPERATIONAL = "daily allocated production (EC DAY001 daily reports)"
ACCOUNTING = "production accounting (monthly volumes on the custody meters)"
LEDGER = "cost ledger (operating cost booked by cost element)"


def _window(conn, since: str | None, until: str | None, days: int) -> tuple[str, str]:
    until = until or (date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)).isoformat()
    return since or (date.fromisoformat(until) - timedelta(days=days)).isoformat(), until


def _in(since: str, until: str):
    return "f->>'period_start' >= %(s)s and f->>'period_start' < %(u)s", {"s": since, "u": until}


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days + 1


def _num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def reconcile(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Every accounting month starting in [since, until) (default the `days` to the latest daily report), oil and
    gas, against the daily reports over the same period; months with daily reports and no accounted volume too."""
    since, until = _window(conn, since, until, days)
    where, params = _in(since, until)
    booked = ops._rows(conn, "accounting_volume", where, params)
    reports = sorted(ops._rows(conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s",
                               {"s": since, "u": until}), key=lambda r: r["f"]["report_date"])
    rows, seen = [], set()
    for b in sorted(booked, key=lambda r: (r["f"].get("period", ""), r["f"].get("product", ""))):
        f = b["f"]
        product = f.get("product")
        if product not in PRODUCTS:
            continue
        field, unit = PRODUCTS[product]
        start, end = str(f.get("period_start"))[:10], str(f.get("period_end"))[:10]
        seen.add((f.get("period"), product))
        inside = [r for r in reports if start <= r["f"]["report_date"][:10] <= end and _num(r["f"].get(field)) is not None]
        op = sum(_num(r["f"][field]) for r in inside)
        acct = _num(f.get("volume"))
        expected = _days(start, end)
        row = {"period": f.get("period"), "product": product, "unit": f.get("unit") or unit,
               "accounting": {"value": acct, "status": f.get("status") or "unknown", "since": start, "through": end,
                              "comment": f.get("comment") or None, "record": b["id"], "source": ACCOUNTING},
               "operational": {"value": round(op) if inside else None, "days": len(inside), "days_expected": expected,
                               "since": inside[0]["f"]["report_date"][:10] if inside else None,
                               "through": inside[-1]["f"]["report_date"][:10] if inside else None,
                               "refs": [r["id"] for r in inside][:REFS], "source": OPERATIONAL}}
        diff = round(op - acct) if inside and acct is not None else None
        pct = round(100 * (op - acct) / acct, 2) if diff is not None and acct else None
        row |= {"difference": diff, "difference_pct": pct}
        if not inside:
            verdict, why = "no_operational", f"no daily report between {start} and {end}"
        elif acct is None:
            verdict, why = "no_operational", "the accounted volume is blank"
        elif row["accounting"]["status"] != "final":
            short = (f"; daily reports cover {len(inside)} of its {expected} days" if len(inside) < expected else "")
            verdict, why = "preliminary", (f"accounting hasn't closed {f.get('period')} "
                                           f"({row['accounting']['status']}){short}")
        elif len(inside) < expected:
            verdict, why = "incomplete", (f"daily reports cover {len(inside)} of the {expected} days from {start} to "
                                          f"{end}, so their sum isn't comparable")
        elif abs(pct) <= TOLERANCE_PCT:
            verdict, why = "agrees", f"within {TOLERANCE_PCT:g} % of the accounted volume"
        else:
            verdict, why = "differs", (f"the daily reports sum {pct:+g} % against the accounted volume; for production "
                                       "accounting to resolve")
        rows.append(row | {"verdict": verdict, "why": why})
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in reports:
        by_month[r["f"]["report_date"][:7]].append(r)
    for month, rs in sorted(by_month.items()):
        if f"{month}-01" < since:  # the window opens mid-month: that month belongs to the window before
            continue
        for product, (field, unit) in PRODUCTS.items():
            if (month, product) in seen or not any(_num(r["f"].get(field)) is not None for r in rs):
                continue
            rows.append({"period": month, "product": product, "unit": unit, "accounting": None,
                         "operational": {"value": round(sum(_num(r["f"].get(field)) or 0 for r in rs)), "days": len(rs),
                                         "since": rs[0]["f"]["report_date"][:10],
                                         "through": rs[-1]["f"]["report_date"][:10],
                                         "refs": [r["id"] for r in rs][:REFS], "source": OPERATIONAL},
                         "difference": None, "difference_pct": None, "verdict": "no_accounting",
                         "why": f"no accounted {product} volume for {month}"})
    rows.sort(key=lambda r: (r["period"], r["product"]), reverse=True)
    count = defaultdict(int)
    for r in rows:
        count[r["verdict"]] += 1
    return {"since": since, "until": until, "tolerance_pct": TOLERANCE_PCT,
            "sources": {"operational": OPERATIONAL, "accounting": ACCOUNTING}, "months": rows,
            "summary": {v: count[v] for v in ("agrees", "differs", "preliminary", "incomplete", "no_accounting",
                                              "no_operational")},
            "note": "The accounted volume is the authority; a difference is shown for production accounting to "
                    "resolve and nothing is restated. Operational figures are the daily allocated reports."}


def unit_cost(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Per month and currency, the ledger's booked operating cost over that month's closed accounted oil, and the
    window's figure over the months that have both. Never summed across currencies."""
    since, until = _window(conn, since, until, days)
    where, params = _in(since, until)
    oil = {}
    for b in ops._rows(conn, "accounting_volume", where + " and f->>'product' = 'oil'", params):
        oil[b["f"].get("period")] = b
    cost: dict[tuple[str, str], dict] = {}
    for e in ops._rows(conn, "ledger_entry", where, params):
        f = e["f"]
        cur = str(f.get("currency") or "").strip().upper() or "unknown"
        c = cost.setdefault((f.get("period"), cur), {"amount": 0.0, "entries": 0, "without_amount": 0, "refs": [],
                                                       "elements": defaultdict(float)})
        c["entries"] += 1
        c["refs"].append(e["id"])
        amt = _num(f.get("amount"))
        if amt is None:
            c["without_amount"] += 1
        else:
            c["amount"] += amt
            c["elements"][f.get("cost_element") or "unknown"] += amt
    rows = []
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for (period, cur), c in sorted(cost.items(), reverse=True):
        b = oil.get(period)
        vol = _num(b["f"].get("volume")) if b else None
        closed = b is not None and b["f"].get("status") == "final" and vol
        per = round(c["amount"] / vol, 2) if closed else None
        rows.append({"period": period, "currency": cur, "cost": round(c["amount"]), "entries": c["entries"],
                     "entries_without_amount": c["without_amount"],
                     "by_element": {k: round(v) for k, v in sorted(c["elements"].items())},
                     "oil_sm3": vol, "oil_status": b["f"].get("status") if b else None,
                     "per_sm3": per, "why_unknown": None if closed else
                     (f"no accounted oil for {period}" if not b else f"{period}'s oil isn't closed in accounting"),
                     "refs": c["refs"][:REFS] + ([b["id"]] if b else [])})
        if closed and not c["without_amount"]:
            totals[cur][0] += c["amount"]
            totals[cur][1] += vol
    currencies = sorted({cur for _, cur in cost}, key=lambda c: (c == "unknown", c))
    return {"since": since, "until": until, "unit": "per accounted Sm3 of oil",
            "sources": {"cost": LEDGER, "volume": ACCOUNTING}, "currencies": currencies,
            "window": {cur: {"cost": round(t[0]), "oil_sm3": round(t[1]), "per_sm3": round(t[0] / t[1], 2)}
                       for cur, t in totals.items() if t[1]},
            "months": rows,
            "note": "Booked operating cost over the month's closed accounted oil, each currency apart and never "
                    "converted; gas isn't converted to oil equivalent. A month without closed oil has no unit cost."}

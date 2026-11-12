"""Schedule compliance and repeat repair rate (#373): does planned work finish when it was planned to, and do
repairs hold? Measured on the work order records, never on people or crews.

    trend(conn, since, until)   per quarter (or month): orders finished, how many by their basic finish (schedule compliance),
                                corrective orders started, and how many came within REPEAT_DAYS of a corrective
                                order on the same equipment finishing (repeat repair rate)

A period with fewer than MIN_ORDERS orders behind a rate reports too little data rather than a percentage. Order
status and dates say what was recorded: a late finish may be a planned date moved up, and a repeat may be a different
fault on the same equipment.
"""

from collections import defaultdict
from datetime import date, timedelta

from ..graph import operations as ops

REPEAT_DAYS = 30
MIN_ORDERS = 5
CLOSED = ("TECO", "CLSD")
CORRECTIVE = ("PM01",)


def _d(v) -> str | None:
    s = str(v or "")[:10]
    return s if len(s) == 10 else None


def _period(d: str, by: str) -> str:
    return d[:7] if by == "month" else f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"


def trend(conn, since: str | None = None, until: str | None = None, days: int = 730, by: str = "quarter") -> dict:
    """Schedule compliance and repeat repair rate per quarter (or month) in [since, until) (default the `days` to the
    latest daily report, from a whole month)."""
    given = since
    since, until = ops._window(conn, since, until, days)
    if not given and since[8:10] != "01":
        since = (date.fromisoformat(since[:8] + "01") + timedelta(days=32)).replace(day=1).isoformat()
    orders = ops._rows(conn, "work_order")
    on = defaultdict(set)
    for r in conn.execute("select src, dst from edge where rel = 'concerns' and src = any(%s)", ([o["id"] for o in orders],)):
        on[r["src"]].add(r["dst"])
    months: dict[str, dict] = {}

    def month(d: str) -> dict:
        m = _period(d, by)
        return months.setdefault(m, {"period": m, "finished": 0, "on_time": 0, "late": [], "no_due_date": 0,
                                     "corrective": 0, "repeats": []})

    finishes = defaultdict(list)  # equipment -> [(finish, order id)] of closed corrective orders
    for o in orders:
        f = o["f"]
        if f.get("order_type") in CORRECTIVE and f.get("status") in CLOSED and _d(f.get("finish")):
            for e in on[o["id"]]:
                finishes[e].append((_d(f["finish"]), o["id"]))
    for o in orders:
        f = o["f"]
        fin, due, start = _d(f.get("finish")), _d(f.get("basic_finish")), _d(f.get("start"))
        if f.get("status") in CLOSED and fin and since <= fin < until:
            m = month(fin)
            m["finished"] += 1
            if not due:
                m["no_due_date"] += 1
            elif fin <= due:
                m["on_time"] += 1
            else:
                m["late"].append({"id": o["id"], "order": o["label"], "due": due, "finish": fin,
                                  "days_late": (date.fromisoformat(fin) - date.fromisoformat(due)).days,
                                  "type": f.get("order_type")})
        if f.get("order_type") in CORRECTIVE and start and since <= start < until:
            m = month(start)
            m["corrective"] += 1
            lo = (date.fromisoformat(start) - timedelta(days=REPEAT_DAYS)).isoformat()
            before = sorted((fx, oid) for e in on[o["id"]] for fx, oid in finishes[e] if oid != o["id"] and lo <= fx <= start)
            if before:
                m["repeats"].append({"id": o["id"], "order": o["label"], "start": start, "after": before[-1][1],
                                     "previous_finish": before[-1][0], "text": f.get("text")})
    rows = []
    for m in sorted(months.values(), key=lambda m: m["period"]):
        dated = m["finished"] - m["no_due_date"]
        rows.append(m | {"schedule_compliance_pct": round(100 * m["on_time"] / dated, 1) if dated >= MIN_ORDERS else None,
                         "repeat_rate_pct": round(100 * len(m["repeats"]) / m["corrective"], 1)
                         if m["corrective"] >= MIN_ORDERS else None})
    fin = sum(r["finished"] - r["no_due_date"] for r in rows)
    ontime = sum(r["on_time"] for r in rows)
    corr = sum(r["corrective"] for r in rows)
    reps = sum(len(r["repeats"]) for r in rows)
    return {"since": since, "until": until, "by": by, "periods": rows,
            "summary": {"finished_with_due_date": fin, "on_time": ontime, "late": fin - ontime,
                        "schedule_compliance_pct": round(100 * ontime / fin, 1) if fin >= MIN_ORDERS else None,
                        "corrective": corr, "repeats": reps,
                        "repeat_rate_pct": round(100 * reps / corr, 1) if corr >= MIN_ORDERS else None,
                        "no_due_date": sum(r["no_due_date"] for r in rows)},
            "thresholds": {"repeat_days": REPEAT_DAYS, "min_orders": MIN_ORDERS},
            "source": "SAP work orders (finish, basic finish, order type, equipment)",
            "note": f"Schedule compliance is closed orders finished by their basic finish; a repeat is a corrective "
                    f"order started within {REPEAT_DAYS} days of a corrective order on the same equipment finishing. "
                    f"A {by} with fewer than {MIN_ORDERS} orders behind a rate says too little data. These measure "
                    "the records, not people or crews: a late finish may be a planned date moved up, and a repeat "
                    "may be a different fault on the same equipment."}

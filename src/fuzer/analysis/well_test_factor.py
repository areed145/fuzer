"""Well test against allocated oil, field-wide and over time (#338): the oil the daily report books against what the
producing wells offer at their latest tested rates. The per-meter factor (metering.allocation_factor) asks whether one
meter reads true; this asks whether the tests the allocation rests on still describe the wells, month by month.

    trend(conn, since, until)   per month: allocated oil, tested offer, their ratio, and the age of the tests used

A well's offer on a day is its latest test within TEST_DAYS (a test of zero oil counts as shut in), cut by the hours a
downtime record says it was down that day. Days with a downtime on a facility or a tag are left out, since the records
don't say which wells it shut in, and so are days the field booked under a third of its usual oil (a field trip). Well
status history isn't loaded, so a well shut in without a downtime record still offers its last test: the factor reads
low for it, and the result says so.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from ..graph import operations as ops
from .metering import AF_HIGH, AF_LOW, AF_SHIFT, IDLE_SHARE, TEST_DAYS

DRIFT_MONTHS = 3  # months at each end compared for a drift


def _t(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)[:16]) if v else None
    except ValueError:
        return None


def _hours(start: datetime, end: datetime) -> dict[str, float]:
    """Hours of [start, end) falling on each day."""
    out, t = {}, start
    while t < end:
        nxt = min(datetime.combine(t.date() + timedelta(days=1), datetime.min.time()), end)
        out[t.date().isoformat()] = out.get(t.date().isoformat(), 0.0) + (nxt - t).total_seconds() / 3600
        t = nxt
    return out


def trend(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Per month in [since, until) (default the `days` to the latest daily report): allocated oil against the wells'
    tested offer, with shut-in wells taken out by their downtime."""
    given = since
    since, until = ops._window(conn, since, until, days)
    if not given and since[8:10] != "01":  # whole months: start at the first month wholly in the window
        since = (date.fromisoformat(since[:8] + "01") + timedelta(days=32)).replace(day=1).isoformat()
    booked = {r["f"]["report_date"][:10]: ops._num(r["f"].get("oil_sm3")) for r in ops._rows(
        conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s", {"s": since, "u": until})}
    if not booked:
        return {"since": since, "until": until, "months": [], "summary": {"months": 0}, "verdict": "no_daily_report", "flags": [],
                "note": "No daily report in the window."}
    usual = median(booked.values())
    wells = [r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where kind = 'well' and retired_at is null and merged_into is null")]
    tests: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for r in conn.execute(
            "select fuzer_id, at, value from measurement where method = 'well test' and quantity = 'oil_rate' "
            "and fuzer_id = any(%s) and at >= %s and at < %s order by at",
            (wells, date.fromisoformat(since) - timedelta(days=TEST_DAYS), until)):
        tests[r["fuzer_id"]].append((r["at"], float(r["value"] or 0)))
    down: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # well -> day -> hours down
    upset: dict[str, list[str]] = defaultdict(list)  # day -> facility or tag downtime records
    for r in conn.execute(
            "select d.fuzer_id as id, t.kind, e.dst, jsonb_object_agg(g.field, g.value) as f from entity d "
            "join golden g using (fuzer_id) join edge e on e.src = d.fuzer_id and e.rel in ('affects', 'caused_by') "
            "join entity t on t.fuzer_id = e.dst where d.kind = 'downtime' and d.retired_at is null "
            "group by d.fuzer_id, t.kind, e.dst"):
        a, b = _t(r["f"].get("start")), _t(r["f"].get("end"))
        if not a:
            continue
        b = b or a + timedelta(hours=ops._num(r["f"].get("duration_h")) or 24)
        for day, h in _hours(a, b).items():
            if not since <= day < until:
                continue
            if r["kind"] == "well":
                down[r["dst"]][day] = min(24.0, down[r["dst"]][day] + h)
            elif r["id"] not in upset[day]:
                upset[day].append(r["id"])
    months: dict[str, dict] = {}
    for day in sorted(booked):
        m = months.setdefault(day[:7], {"month": day[:7], "allocated_sm3": 0.0, "offered_sm3": 0.0,
                                         "offered_ignoring_downtime_sm3": 0.0, "days": 0, "days_left_out": 0,
                                         "left_out": {"facility_or_tag_downtime": 0, "field_trip": 0},
                                         "ages": [], "wells": set(), "well_days_shut_in": 0.0, "no_live_test": set()})
        if upset.get(day):
            m["days_left_out"] += 1
            m["left_out"]["facility_or_tag_downtime"] += 1
            continue
        if usual > 0 and booked[day] < IDLE_SHARE * usual:
            m["days_left_out"] += 1
            m["left_out"]["field_trip"] += 1
            continue
        end = datetime.fromisoformat(day) + timedelta(days=1)
        offer = raw = 0.0
        for w in wells:
            live = [(at, q) for at, q in tests.get(w, ()) if end - timedelta(days=TEST_DAYS) < at < end]
            if not live:
                if tests.get(w):
                    m["no_live_test"].add(w)
                continue
            at, q = live[-1]
            if q <= 0:
                continue
            up = 1 - down[w].get(day, 0.0) / 24
            raw += q
            offer += q * up
            m["well_days_shut_in"] += 1 - up
            m["wells"].add(w)
            m["ages"].append((end - at).days)
        m["days"] += 1
        m["allocated_sm3"] += booked[day]
        m["offered_sm3"] += offer
        m["offered_ignoring_downtime_sm3"] += raw
    rows = []
    for m in months.values():
        f = m["allocated_sm3"] / m["offered_sm3"] if m["offered_sm3"] else None
        f0 = m["allocated_sm3"] / m["offered_ignoring_downtime_sm3"] if m["offered_ignoring_downtime_sm3"] else None
        rows.append({k: m[k] for k in ("month", "days", "days_left_out", "left_out")} | {
            "allocated_sm3": round(m["allocated_sm3"]), "offered_sm3": round(m["offered_sm3"]),
            "factor": round(f, 3) if f else None, "factor_ignoring_downtime": round(f0, 3) if f0 else None,
            "wells_offering": len(m["wells"]), "well_days_shut_in": round(m["well_days_shut_in"], 1),
            "wells_without_a_live_test": len(m["no_live_test"]),
            "median_test_age_days": median(m["ages"]) if m["ages"] else None,
            "max_test_age_days": max(m["ages"]) if m["ages"] else None})
    fs = [r["factor"] for r in rows if r["factor"]]
    level = round(median(fs), 3) if fs else None
    first = median(fs[:DRIFT_MONTHS]) if len(fs) >= 2 * DRIFT_MONTHS else None
    last = median(fs[-DRIFT_MONTHS:]) if len(fs) >= 2 * DRIFT_MONTHS else None
    drift = round(last - first, 3) if first is not None else None
    flags = ([] if level is None else ["low"] if level < AF_LOW else ["high"] if level > AF_HIGH else []) + (
        ["drifting"] if drift is not None and abs(drift) >= AF_SHIFT else [])
    verdict = "no_tests" if level is None else flags[0] if flags else "steady"
    return {"since": since, "until": until, "months": rows, "verdict": verdict, "flags": flags,
            "summary": {"months": len(rows), "factor": level, "first_months_factor": first and round(first, 3),
                        "latest_months_factor": last and round(last, 3), "drift": drift,
                        "days_used": sum(r["days"] for r in rows), "days_left_out": sum(r["days_left_out"] for r in rows),
                        "band": [AF_LOW, AF_HIGH], "drift_threshold": AF_SHIFT, "drift_months": DRIFT_MONTHS,
                        "test_days": TEST_DAYS},
            "source": "daily report oil (allocated), well tests (oil rate), downtime records",
            "note": f"Allocated oil over what the producing wells offer at their latest test within {TEST_DAYS} days, "
                    "cut by each well's own downtime. Days with a facility or tag downtime, or a field trip, are left "
                    "out. Well status history isn't loaded, so a well shut in with no downtime record still offers its "
                    "last test and pulls the factor down. Off one, the tests or the allocation are suspect; which "
                    "stays a question for the engineer."}

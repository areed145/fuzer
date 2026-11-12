"""How the plant is running (P7.3a, #353): one cited scorecard across production, maintenance and reliability, this
period against the one before it, so a question like "how did we do this month?" reads one view instead of an assistant
rebuilding it each time.

    production efficiency    oil allocated against potential, from the daily reports
    deferred oil             oil lost to downtime events that started in the period
    flare                    gas flared, from the daily reports
    corrective backlog       corrective orders open at the end of the period
    preventive on time       preventive and inspection orders due in the period that finished by their due date
    failures                 failure notifications on equipment in the period
    repeat failures          equipment with more than one failure in the period

Each KPI carries its window, unit and source, and the records behind each value. A KPI the records can't say for a
period (no daily reports in it, no orders due, no notifications loaded that far back) is unknown, never zero. This is
internal evidence for a conversation, not an approved report.
"""

from datetime import date, timedelta

from ..graph import operations as ops
from ..graph.queries import _DOWNTIME

REFS = 50  # records kept behind one value; the count says how many there are


def _production_efficiency(conn, since: str, until: str) -> dict:
    t = ops.daily_totals(conn, since, until, ("oil_sm3", "oil_potential_sm3"))
    if not t["days"]:
        return {"note": "no daily report in the period"}
    if not t["oil_potential_sm3"]["days"] or not t["oil_potential_sm3"]["sum"]:
        return {"note": "the daily reports in the period state no oil potential", "refs": t["refs"]}
    return {"value": round(100 * t["oil_sm3"]["sum"] / t["oil_potential_sm3"]["sum"], 1), "refs": t["refs"],
            "note": None if t["days"] == t["oil_potential_sm3"]["days"]
            else f"{t['days'] - t['oil_potential_sm3']['days']} of {t['days']} days state no potential"}


def _deferred_oil(conn, since: str, until: str) -> dict:
    if not ops.daily_totals(conn, since, until, ())["days"]:
        return {"note": "no daily report in the period, so no downtime record can be trusted to be complete"}
    rows = conn.execute(_DOWNTIME + "select id, f->>'lost_oil_sm3' as lost from dd",
                        {"id": None, "since": since, "until": until}).fetchall()
    if not rows and not conn.execute("select 1 from entity where kind = 'downtime' limit 1").fetchone():
        return {"note": "no downtime records are loaded"}
    return {"value": round(sum(ops._num(r["lost"]) for r in rows)), "refs": [r["id"] for r in rows]}


def _flare(conn, since: str, until: str) -> dict:
    t = ops.daily_totals(conn, since, until, ("flare_ksm3",))
    if not t["flare_ksm3"]["days"]:
        return {"note": "no daily report in the period states flare" if t["days"] else "no daily report in the period"}
    return {"value": round(t["flare_ksm3"]["sum"]), "refs": t["refs"],
            "note": None if t["days"] == t["flare_ksm3"]["days"]
            else f"{t['days'] - t['flare_ksm3']['days']} of {t['days']} days state no flare"}


def _backlog(conn, since: str, until: str) -> dict:
    b = ops.corrective_backlog_at(conn, until)
    if not b["orders"]:
        return {"note": "no corrective work order was created by the end of the period"}
    return {"value": len(b["open"]), "refs": b["open"],
            "note": f"{len(b['unplaced'])} closed orders have no finish date and aren't counted" if b["unplaced"] else None}


def _preventive(conn, since: str, until: str) -> dict:
    p = ops.preventive_on_time(conn, since, until)
    if not p["due"]:
        return {"note": "no preventive or inspection order was due in the period"}
    return {"value": round(100 * len(p["on_time"]) / p["due"], 1), "refs": p["late"],
            "note": f"{len(p['on_time'])} of {p['due']} on time" + ("; the records are the late ones" if p["late"] else "")}


def _notifications_loaded(conn, until: str) -> bool:
    return bool(conn.execute(
        "select 1 from golden g join entity e using (fuzer_id) where e.kind = 'notification' and g.field = 'created' "
        "and g.value #>> '{}' < %s limit 1", (until,)).fetchone())


def _failures(conn, since: str, until: str) -> dict:
    if not _notifications_loaded(conn, until):
        return {"note": "no failure notification is loaded from before the end of the period"}
    items = ops.bad_actors(conn, since, until, limit=100_000)["bad_actors"]
    return {"value": sum(i["failures"] for i in items), "refs": [i["id"] for i in items]}


def _repeats(conn, since: str, until: str) -> dict:
    if not _notifications_loaded(conn, until):
        return {"note": "no failure notification is loaded from before the end of the period"}
    items = [i for i in ops.bad_actors(conn, since, until, limit=100_000)["bad_actors"] if i["failures"] > 1]
    return {"value": len(items), "refs": [i["id"] for i in items]}


# key, label, unit, better, source, definition, compute
KPIS = (
    ("production_efficiency", "Production efficiency", "%", "higher", "daily production reports (EC DAY001)",
     "Oil allocated against the oil potential, summed over the period's days.", _production_efficiency),
    ("deferred_oil", "Deferred oil", "Sm3", "lower", "downtime (deferment) records",
     "Oil lost to downtime events that started in the period.", _deferred_oil),
    ("flare", "Flare", "kSm3", "lower", "daily production reports (EC DAY001)",
     "Gas flared, summed over the period's days.", _flare),
    ("corrective_backlog", "Corrective backlog", "orders", "lower", "SAP work orders (PM01)",
     "Corrective orders open at the end of the period: created before it ended and not finished by then.", _backlog),
    ("preventive_on_time", "Preventive work on time", "%", "higher", "SAP work orders (PM02, PM03)",
     "Of the preventive and inspection orders due in the period, the share finished by their due date.", _preventive),
    ("failures", "Failures", "notifications", "lower", "SAP notifications on equipment",
     "Failure notifications raised on equipment in the period.", _failures),
    ("repeat_failures", "Equipment failing more than once", "units", "lower", "SAP notifications on equipment",
     "Equipment with two or more failure notifications in the period.", _repeats),
)


def _verdict(now, before, better: str) -> str:
    if now is None or before is None:
        return "unknown"
    if now == before:
        return "same"
    return "better" if (now > before) == (better == "higher") else "worse"


def windows(conn, until: str | None = None, days: int = 30) -> list[tuple[str, str]]:
    """This period and the one before it, each `days` long, ending the day after `until` (default the latest daily
    report): [since, until) as ISO dates."""
    end = date.fromisoformat(until) + timedelta(days=1) if until else date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)
    span = timedelta(days=days)
    return [((end - span).isoformat(), end.isoformat()), ((end - 2 * span).isoformat(), (end - span).isoformat())]


def scorecard(conn, until: str | None = None, days: int = 30) -> dict:
    """The KPIs for the period of `days` ending on `until` (default the latest daily report) against the period before
    it. Each KPI: its unit, source, definition, both periods' value, records and note, the change and whether it is
    better, worse, the same or unknown."""
    if days < 1 or days > 366:
        raise ValueError("a period is 1 to 366 days")
    (s1, u1), (s0, u0) = windows(conn, until, days)
    period = lambda s, u: {"since": s, "through": (date.fromisoformat(u) - timedelta(days=1)).isoformat()}
    out = []
    for key, label, unit, better, source, definition, compute in KPIS:
        now, before = compute(conn, s1, u1), compute(conn, s0, u0)

        def side(r: dict, s: str, u: str) -> dict:
            refs = r.get("refs") or []
            return {**period(s, u), "value": r.get("value"), "records": len(refs), "refs": refs[:REFS],
                    "note": r.get("note")}
        a, b = now.get("value"), before.get("value")
        out.append({"key": key, "label": label, "unit": unit, "better": better, "source": source,
                    "definition": definition, "now": side(now, s1, u1), "before": side(before, s0, u0),
                    "change": round(a - b, 1) if a is not None and b is not None else None,
                    "verdict": _verdict(a, b, better)})
    return {"days": days, "now": period(s1, u1), "before": period(s0, u0), "kpis": out,
            "unknown": [k["label"] for k in out if k["verdict"] == "unknown"],
            "note": "Internal evidence from the loaded records, not an approved report. Unknown means the records "
                    "can't say, never zero."}

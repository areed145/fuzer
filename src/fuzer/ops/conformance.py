"""Conformance (P8.2): for each asset, the operating model it runs, where it differs and whether the differences are
justified, and whether its work follows the standard in practice.

    standing(conn, days)   this project: {project, model, version, current, applied, deviations: [{part, key, change,
                           reason, owner, review_by, overdue}], unnamed, stale, overdue, practice, verdict}
    fleet(allowed, days)   standing for every project a person may open, side by side, the rest named as left out

A deviation is justified when it is named (a reason and an owner) and not past its review date. Past it, the
`deviation_reviews` detector raises `deviation_review_due` for the deviation's owner: keep it with a new review date,
make it the standard (P8.3), or bring the asset back in line.

In practice, over the last `days`:

    sla           person steps due in the window: finished on time, finished late or still open past due
    overrides     cases sent to the field though not ready, and field work started without its permit (P5.5)
    skipped       person steps a case closed around: cancelled while the case closed, never done
    rounds        operator rounds done, partly done and missed (P5.4c)

`verdict` is `standard` (applied as it is, every deviation named and in date), `justified` (it differs only where it
says why), or `drifted` (an unnamed difference, an overdue review, or not applied).
"""

from datetime import UTC, date, datetime, timedelta

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, fact

DAYS = 28


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("deviation_review_due") or exception_type(
        "deviation_review_due", "Deviation review due", role="role:ops_lead", audience=("ioc",), joins=False,
        description="A named deviation from the operating model is past its review date. Keep it with a new review "
                    "date and why, propose it as the standard, or bring the asset back in line.")


REVIEW_TYPE = _type().name


def _practice(conn, days: int) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    sla = conn.execute(
        """
        select count(*) filter (where t.finished_at is not null and t.finished_at <= t.due_at)::int as on_time,
               count(*) filter (where t.finished_at > t.due_at)::int as late,
               count(*) filter (where t.finished_at is null and t.state in ('ready', 'waiting'))::int as open_past_due
        from work_token t where t.kind in ('user', 'fix') and t.due_at >= %s and t.due_at < now()
        """, (since,)).fetchone()
    due = sla["on_time"] + sla["late"] + sla["open_past_due"]
    sent = conn.execute(
        "select count(*)::int as n from event_outbox where topic = 'exception.dispatched' and created_at >= %s "
        "and coalesce(payload->>'override', '') <> ''", (since,)).fetchone()["n"] if _has(conn, "event_outbox") else 0
    started = conn.execute(
        "select count(*)::int as n from gate_decision where kind = 'field_start_without_permit' and at >= %s",
        (since,)).fetchone()["n"]
    skipped = conn.execute(
        """
        select count(*)::int as n from work_token t join work_item i on i.id = t.item_id
        where t.kind in ('user', 'fix') and t.state = 'cancelled' and t.attached_to is null
          and i.closed_at >= %s
        """, (since,)).fetchone()["n"]
    rounds = conn.execute(
        """
        select count(*)::int as rounds, count(*) filter (where state = 'done')::int as done,
               count(*) filter (where state = 'partly_done')::int as partly_done,
               count(*) filter (where state = 'missed')::int as missed
        from round_run where shift_start >= %s and state <> 'open'
        """, (since,)).fetchone() if _has(conn, "round_run") else {"rounds": 0, "done": 0, "partly_done": 0, "missed": 0}
    return {"days": days,
            "sla": {**sla, "due": due, "met_pct": round(100 * sla["on_time"] / due, 1) if due else None},
            "overrides": {"sent_not_ready": sent, "started_without_permit": started, "total": sent + started},
            "skipped": skipped,
            "rounds": {**rounds, "done_pct": round(100 * rounds["done"] / rounds["rounds"], 1) if rounds["rounds"] else None}}


def _has(conn, table: str) -> bool:
    return conn.execute("select to_regclass(%s) is not null as ok", (table,)).fetchone()["ok"]


def standing(conn, days: int = DAYS) -> dict:
    """This project against its operating model, and its work in practice over the last `days`."""
    from ..core import projects
    from ..workflow import operating_model as om

    project = projects.current(conn)
    try:
        pkg = om.conformance_package(conn)
    except om.OperatingModelError as e:
        return {"project": project, "model": None, "verdict": "no_model", "note": str(e),
                "practice": _practice(conn, days)}
    c = om.conformance(conn, pkg)
    names = {(n["part"], n["key"]): n for n in om.named(conn)}
    rows = []
    for d in om.deviations(conn, pkg):
        n = names.get((d["part"], d["key"])) or {}
        rows.append({"part": d["part"], "key": d["key"], "change": d["change"], "reason": d["reason"],
                     "owner": d["owner"], "review_by": n.get("review_by") and n["review_by"].isoformat(),
                     "overdue": bool(n.get("overdue")), "named": bool(d["owner"])})
    overdue = [r for r in rows if r["overdue"]]
    unnamed = [r for r in rows if not r["named"]]
    verdict = ("drifted" if unnamed or overdue or not c["applied"] else "justified" if rows else "standard")
    return {"project": project, "model": c["model"], "version": c["version"],
            "current": c["version"] == om.STANDARD_VERSION if c["model"] == om.STANDARD_NAME else None,
            "applied": c["applied"], "deviations": rows, "named": c["named"], "unnamed": len(unnamed),
            "overdue": len(overdue), "stale": c["stale"], "verdict": verdict, "practice": _practice(conn, days)}


def fleet(allowed: list[str] | None, days: int = DAYS) -> dict:
    """Each project in `allowed` (None: every project), its standing; and the stored projects left out (D6: a fleet
    view is a union of grants, never a bypass)."""
    from ..core import db, projects

    with db.connect() as conn:
        names = projects.stored(conn)
    shown = [n for n in names if allowed is None or n in allowed]
    out = []
    for name in shown:
        with db.connect() as conn, projects.use(conn, name), db.connect() as scoped:
            try:
                out.append(standing(scoped, days))
            except Exception as e:  # one asset's register failing never hides the others
                out.append({"project": name, "error": str(e).splitlines()[0][:200]})
    return {"days": days, "projects": out, "left_out": [n for n in names if n not in shown],
            "current": {"model": "standard", "version": _standard_version()}}


def _standard_version() -> int:
    from ..workflow import operating_model as om

    return om.STANDARD_VERSION


@detectors.register("deviation_reviews", title="A named deviation from the operating model past its review date",
                    kind=detectors.RULE, cadence="daily", raises=(REVIEW_TYPE,),
                    needs={"a named deviation": lambda conn, pack: _named_any(conn)},
                    eval="test:tests/evals/test_conformance.py::"
                         "test_a_deviation_past_its_review_is_work_for_its_owner_until_it_is_reviewed",
                    found=lambda out: len(out["raised"]))
def reviews(conn, pack=None, by: str = "agent:standards") -> dict:
    """Each named deviation past its review date raised as `deviation_review_due` for its owner (keyed by part and
    key); open ones whose deviation was reviewed (a later date) or unnamed are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception
    from ..workflow import operating_model as om

    roles = {r["name"] for r in conn.execute("select name from work_role")}
    out, due = {"checked": 0, "raised": [], "cleared": 0, "errors": []}, set()
    for d in om.named(conn):
        out["checked"] += 1
        if not d["overdue"]:
            continue
        key = f"{d['part']}:{d['key']}"
        due.add(key)
        found = Finding(
            "standards.deviation_review", DETERMINISTIC, method="Named deviations read against their review date",
            start=d["review_by"].isoformat(), data={"part": d["part"], "key": d["key"]},
            items=(fact("Differs at", f"{d['part']} {d['key']}"), fact("Why", d["reason"]), fact("Owner", d["owner"]),
                   fact("Named", f"{d['named_at']:%Y-%m-%d} by {d['named_by']}"),
                   fact("Review was due", d["review_by"].isoformat()),
                   fact("Overdue by", (date.today() - d["review_by"]).days, "days")))
        try:
            res = raise_exception(conn, REVIEW_TYPE, key, f"Review the {d['part']} deviation {d['key']}", by,
                                  source="standards", finding=found,
                                  first_role=f"role:{d['owner']}" if d["owner"] in roles else None)
        except (work.WorkError, ExceptionError) as e:
            out["errors"].append(str(e))
            continue
        out["raised"].append({"exception_id": res["id"], "key": key, "new": res["raised"]})
    for r in conn.execute("select key from exception_record where type = %s and state = 'open' and source = 'standards'",
                          (REVIEW_TYPE,)).fetchall():
        if r["key"] not in due:
            clear_exception(conn, REVIEW_TYPE, r["key"], by)
            out["cleared"] += 1
    conn.commit()
    return out


def _named_any(conn) -> bool:
    from ..core import projects

    return _has(conn, "operating_model_deviation") and conn.execute(
        "select 1 from operating_model_deviation where project = %s limit 1",
        (projects.current(conn),)).fetchone() is not None

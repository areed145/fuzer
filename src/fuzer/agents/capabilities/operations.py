"""Operations capabilities, declared with `capability` (#133)."""

from typing import Any, Literal

from ...core import db
from ...graph import queries
from ...workflow import engine as work
from ...workflow import work_drafts
from ..mcp_server import AgentRole, _chart, _plain, _resolve, _work, capability
from . import ASKER


@capability("all", kind="query")
def board(area: str | None = None, unit: str | None = None, kind: str | None = None, text: str | None = None,
          since: str | None = None, limit: int = 10) -> dict[str, Any]:
    """The work board (#99): outstanding work across SAP backlog, PMs and inspections, incidents and SRE
    upsets, workflow items, opportunities, reviews and projects, one card per piece of work, ranked by value
    (#100). Each card: rank, value and why, its records, whether a person pinned it and why it moved.
    Filter by area or unit (any ref), kind, words in the title, or new since a time. "Top 5 on train B":
    unit or area, limit 5."""
    from ...ops import board as b
    with db.connect() as conn:
        args = {}
        for k, ref in (("area", area), ("unit", unit)):
            if ref:
                fid, err = _resolve(conn, ref)
                if err:
                    return err
                args[k] = fid
        out = b.cards(conn, kind, args.get("area"), args.get("unit"), text, since, 1, min(limit, 50))
    for c in out["cards"]:
        c.pop("breakdown", None)
    return _plain(out)


@capability(kind="query")
def next_work(agent: str, limit: int = 5) -> dict[str, Any]:
    """The workflow steps your agent role can take now, highest on the board first. Claim one with
    claim_work."""
    from ...ops import board as b
    with db.connect() as conn:
        return _plain({"agent": agent, "steps": b.next_work(conn, agent, min(limit, 20))})


@capability("all", kind="query")
def get_daily_ops_report(day: str | None = None, version: int | None = None) -> dict[str, Any]:
    """The daily operations report for a production day (YYYY-MM-DD; default the latest): headline,
    production and deferment, upsets and events, wells, maintenance and integrity, look-ahead, every line
    citing its records. With the checks (uncited lines, numbers not in the records) and what changed since
    the previous version. For "what happened on the 28th?"."""
    from ...ops import daily_report
    with db.connect() as conn:
        if day is None:
            got = daily_report.days(conn, 1)
            day = got[0]["day"] if got else None
        out = daily_report.get(conn, day, version) if day else None
    return _plain(out) if out else {"error": f"no report for {day}" if day else "no reports yet"}


@capability("all", kind="query")
def what_changed(since: str, until: str | None = None, limit: int = 20) -> dict[str, Any]:
    """What happened in the records between two times (plant time): new log events, notifications, work
    orders started and finished, downtime started and ended, incidents, equipment installed or removed,
    limits taking or ending effect, excursions and new well tests, with counts. Start here for "what
    happened since ..." and each shift's or run's snapshot. since / until ISO times (until: now)."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.what_changed(conn, since, until, min(limit, 100)))


@capability("field-operations-facilities", "asset-performance-reporting", kind="calc")
def records_mismatch(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Records that don't agree: downtime records with no operator log entry (or, for equipment reasons, no
    notification) about the same plant within the tolerances, and log entries that say something stopped with no
    downtime record. States the tolerances, the window and the time the logs run to. Each mismatch is a question for
    the shift, not proof of a missing event. since / until ISO times (default: the 30 days up to the latest log)."""
    from ...analysis import records
    with db.connect() as conn:
        return _plain(records.mismatch(conn, since, until))


@capability(
    "production-surveillance", "wells-subsurface", "maintenance-integrity", "work-management-supply-chain",
    "asset-performance-reporting", kind="query"
)
def deferment_pareto(by: Literal["reason", "category", "tag", "class", "well"] = "reason",
                     since: str | None = None, until: str | None = None, limit: int = 15) -> dict[str, Any]:
    """Deferred (lost) oil ranked by downtime reason, reason category, causing tag, its ISO 14224 class,
    or affected well, with each one's share. Default: the last 12 months."""
    from ...graph import operations
    with db.connect() as conn:
        return _plain(operations.deferment_pareto(conn, by, since, until, min(limit, 100)))


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    "asset-performance-reporting", kind="query"
)
def well_performance(ref: str | None = None, since: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Well tests. With ref (a well): its test history (oil rate, water cut, GOR, lift gas, WHP). Without:
    wells ranked by oil rate decline over the window, water cut risers, and deferment per well.
    Default window: 12 months."""
    from ...graph import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.well_performance(conn, fid, since, min(limit, 100)))


@capability("production-surveillance", "wells-subsurface", "field-operations-facilities", kind="query")
def gas_lift_network(ref: str | None = None, since: str | None = None, limit: int = 15) -> dict[str, Any]:
    """The gas lift network: compression -> lift gas headers -> jacket gas lift manifolds (some through
    another jacket) -> gas lifted wells. Per header: jackets, wells, lift gas and oil from latest tests. Per
    well: lift gas, oil per lift gas and measured response to more lift gas (Sm3/d oil per extra kSm3/d),
    least and most responsive wells, wells lifting mostly water. With ref (header, manifold, jacket,
    compressor or well): only that part, plus its supply path. Default window: 12 months of tests."""
    from ...graph import operations
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain(operations.gas_lift_network(conn, fid, since, min(limit, 100)))


@capability(kind="query")
def list_reviews(kind: Literal["match", "conflict"] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Open questions waiting for a person: possible duplicate records ("match") and sources that
    disagree on a value ("conflict"). Decisions are made by people in the Fuzer web UI."""
    with db.connect() as conn:
        rows = queries.reviews(conn, "open", kind, min(limit, 200))
    return _plain([{"id": r["id"], "kind": r["kind"], "entity": r["label"], **r["payload"]} for r in rows])


@capability(kind="query")
def my_work(agent: AgentRole, limit: int = 20) -> dict[str, Any]:
    """Your open work as agent role `agent`: agent steps waiting for your role (claimed_by and
    lease_until show whether a worker has one), person steps you claimed or were handed, and work items
    in your queue. Each step has a task_id for claim_work / complete_work / hand_off."""
    return _work(work.agent_inbox, agent, min(limit, 200))


@capability("orchestrator", kind="query")
def draft_work_item(title: str, role: str, summary: str, evidence: list[str],
                    subjects: list[str] | None = None) -> dict[str, Any]:
    """Propose work for a person's queue. This creates nothing: it returns a draft and a marker, and the
    person asking sees it on the page and decides. `role` is a person role such as production_engineer,
    `summary` is Markdown saying what is needed and why (a diagram goes in a ```mermaid fence, with one
    statement per line), `evidence` lists the records behind it (refs, at least one)
    and `subjects` the assets it is about."""
    asker = ASKER.get()
    if asker is None:
        return {"error": "Only someone signed in and asking in the Ask page can receive a draft."}
    with db.connect() as conn:
        ids = {}
        for ref in [*evidence, *(subjects or [])]:
            fid, problem = _resolve(conn, ref)
            if problem:
                return problem
            ids[ref] = fid
        try:
            draft = work_drafts.create(conn, title, role, summary, [ids[r] for r in evidence], asker,
                                       [ids[r] for r in subjects or []])
        except work.WorkError as e:
            return {"error": str(e)}
    return {"draft": draft["id"], "marker": f"[[work-draft:{draft['id']}]]", "queue": role,
            "note": "Put the marker on its own line. Nothing exists yet: say the person must confirm it."}


@capability("asset-performance-reporting", "production-surveillance", "maintenance-integrity", kind="calc")
def kpi_scorecard(until: str | None = None, days: int = 30) -> dict[str, Any]:
    """How the plant is running (#353): production efficiency, deferred oil, flare, corrective backlog, preventive work
    on time, failures and repeat failures for the `days` ending on `until` (YYYY-MM-DD; default the latest daily
    report) against the same length before. Each KPI cites its window, unit, source and records; a KPI the records
    can't say is unknown, never zero. Internal evidence, not an approved report."""
    from ...analysis import kpis
    with db.connect() as conn:
        try:
            return _plain(kpis.scorecard(conn, until, days))
        except ValueError as e:
            return {"error": str(e)}


@capability("asset-performance-reporting", kind="plot")
def plot_kpi_scorecard(until: str | None = None, days: int = 30) -> dict[str, Any]:
    """Chart each KPI's change against the period before, coloured better or worse, unknown ones left as gaps."""
    from ...plots import general
    return _chart(general.scorecard, {"until": until, "days": days})


@capability("asset-performance-reporting", "production-surveillance", kind="calc")
def accounting_reconciliation(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Operational against accounting volumes (#372): each month's daily allocated oil and gas (EC DAY001) summed over
    the accounting period, against what production accounting closed on the custody meters. Each month cites both
    sources, both periods and the records: agrees (within 1 %), differs, preliminary (not closed), incomplete (daily
    reports miss days), no_accounting or no_operational. The accounted volume is the authority; a difference is for
    production accounting to resolve and nothing is restated."""
    from ...analysis import accounting
    with db.connect() as conn:
        return _plain(accounting.reconcile(conn, since, until, days))


@capability("asset-performance-reporting", kind="calc")
def unit_cost(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Operating cost per accounted Sm3 of oil (#372): the cost ledger's booked cost by month and cost element over
    that month's closed accounted oil, each currency apart and never converted or summed. A month whose oil isn't
    closed has no unit cost (unknown, never zero)."""
    from ...analysis import accounting
    with db.connect() as conn:
        return _plain(accounting.unit_cost(conn, since, until, days))


@capability("asset-performance-reporting", kind="plot")
def plot_accounting_reconciliation(since: str | None = None, until: str | None = None, days: int = 365) -> dict[str, Any]:
    """Chart each month's daily allocated oil against production accounting, in % of the accounted volume, with the
    1 % tolerance; months that can't be compared are listed, not drawn."""
    from ...plots import general
    return _chart(general.reconciliation, {"since": since, "until": until, "days": days})


@capability("hse-incident-management", "maintenance-integrity", "field-operations-facilities", kind="query")
def safeguard_status(ref: str | None = None, system: bool = True) -> dict[str, Any]:
    """Safeguards on an item, or across the plant with no ref (P4.2e): each bypass in place (overdue or not, under
    which change), each proof test against its interval (IEC 61511-1 16.3: overdue, or none on file), each safeguard
    demanded in the last year against the rate it was designed for (over it, or failed on a demand; spurious trips
    listed, not counted) and the open change records. `system` widens an item to the rest of its train, by its
    piping. Read from the safeguard register, the bypass register and the demand log, never from tag names."""
    from ...ops import safeguards
    with db.connect() as conn:
        subjects = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
            subjects = [fid]
        tests = safeguards.proof_tests(conn, subjects, system=system)
        demands = safeguards.demands(conn, subjects, system=system)
        return _plain({
            "ref": ref, "system": system,
            "bypasses": safeguards.bypasses(conn, subjects, system=system),
            "proof_tests_overdue": [p for p in tests if p["overdue"]],
            "proof_tests_in_date": len([p for p in tests if not p["overdue"]]),
            "demands": demands,
            "changes": safeguards.changes(conn, subjects, system=system),
            "not_held": "Fuzer holds no safety requirements specification, PFD calculation or proof test procedure; "
                        "a demand rate is read against the rate on the register only."})

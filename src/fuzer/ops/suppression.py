"""Suppression (P3.5b, #693): planned work and shutdowns explain what an asset's detectors see while they run, so a
detection about an asset in such a window is recorded and counted here instead of opening a case. Nothing is lost:
every suppressed finding is kept with its evidence and how many times it was raised, and a condition still holding
when the window ends opens as usual on its next raise.

    covering(conn, subjects, at)   the window a detection about `subjects` at `at` falls in, or None
    record(conn, ...)              keep a suppressed finding (or count it again)
    add(conn, refs, start, end, reason, by) / end(conn, id, by)   a person's own window
    windows(conn) / found(conn)    what is suppressing now, with its counts, and what it held back

Windows come from the plan (`plan_item` scheduled or in progress: a shutdown when it needs one or is a turnaround,
otherwise planned work) and from people. Only types that declare `suppress=True` (detections of a machine's own
condition: a trip, its health, a process upset) are ever suppressed, and never a safety case type (`gates`), so an
integrity window breach or another system's report always opens its case. "About the asset" is read as the join
reads it (`exceptions.anchors`).
"""

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from ..core import events
from ..core.exceptions import ExceptionError
from .exceptions import anchor_map, anchors, match

PLAN_LIVE = ("scheduled", "in_progress")


def _at(finding_evidence: dict | None):
    """When a finding says it happened (its start), or None for now."""
    s = ((finding_evidence or {}).get("finding") or {}).get("start")
    try:
        return datetime.fromisoformat(str(s)[:19]) if s and "T" in str(s) else None
    except ValueError:
        return None


def covering(conn, subjects: list[str], at=None) -> dict | None:
    """The window (planned work, a shutdown or a person's) covering `at` (now when None) on an asset `subjects`
    stand for; the first to end when several do. Shutdowns always count; other planned work while
    `exceptions.suppress_planned_work` is on."""
    from ..core import settings

    if not subjects:
        return None
    planned = bool(settings.get(conn, "exceptions.suppress_planned_work"))
    if isinstance(at, datetime) and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)  # as the plan reads a time without a zone (plan._when)
    rows = conn.execute(
        """
        select 'plan' as src, id, subjects, end_at, title as reason,
               case when needs_shutdown or kind in ('turnaround', 'shutdown') then 'shutdown' else 'planned_work' end as kind
        from plan_item where (%(planned)s or needs_shutdown or kind in ('turnaround', 'shutdown'))
          and status = any(%(live)s) and cardinality(subjects) > 0
          and start_at <= coalesce(%(at)s::timestamptz, now()) and end_at > coalesce(%(at)s::timestamptz, now())
        union all
        select 'person', id, subjects, end_at, reason, 'person' from suppression
        where start_at <= coalesce(%(at)s::timestamptz, now()) and end_at > coalesce(%(at)s::timestamptz, now())
        order by end_at, id
        """, {"live": list(PLAN_LIVE), "at": at, "planned": planned}).fetchall()
    if not rows:
        return None
    mine = anchors(conn, subjects)
    stand = anchor_map(conn, list({s for r in rows for s in r["subjects"]}))
    for r in rows:
        if any(stand.get(s, set()) & mine for s in r["subjects"]):
            return {"kind": r["kind"], "id": r["id"], "reason": r["reason"], "until": r["end_at"]}
    return None


def applies(conn, t) -> bool:
    """Whether a type is ever suppressed: it declares so, and it is no safety case type."""
    from ..core import gates

    return bool(getattr(t, "suppress", False)) and t.name not in gates.safety_case_types(conn)


def record(conn, w: dict, type: str, key: str, title: str, source: str, subjects: list[str], evidence: dict,
           value_usd: float | None) -> dict:
    """Keep a suppressed finding, or count it again: what `raise_` returns instead of an exception."""
    row = conn.execute(
        """
        insert into suppressed_finding (type, key, title, source, subjects, evidence, value_usd, window_kind, window_id,
                                        reason)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (type, key, window_kind, window_id) do update set raised = suppressed_finding.raised + 1,
            title = excluded.title, evidence = excluded.evidence, last_seen = now(),
            subjects = array(select distinct unnest(suppressed_finding.subjects || excluded.subjects)),
            value_usd = coalesce(excluded.value_usd, suppressed_finding.value_usd)
        returning *
        """, (type, key, title, source, subjects, Jsonb(evidence), value_usd, w["kind"], w["id"], w["reason"])).fetchone()
    if row["raised"] == 1:
        events.publish(conn, "exception.suppressed", f"suppressed:{row['id']}",
                       {"type": type, "key": key, "title": title, "subjects": subjects, "window": w["kind"],
                        "window_id": w["id"], "reason": w["reason"]})
    return {"id": None, "item_id": None, "type": type, "key": key, "title": title, "state": "suppressed",
            "subjects": subjects, "raised": False, "suppressed": {**w, "until": w["until"].isoformat()
                                                                  if hasattr(w["until"], "isoformat") else w["until"],
                                                                  "count": row["raised"]}}


def add(conn, refs, start: str, end: str, reason: str, by: str) -> dict:
    """A person's window: detections about these assets from `start` to `end` are counted, not opened."""
    subjects, unmatched = match(conn, refs)
    if unmatched or not subjects:
        raise ExceptionError(f"no asset matches {', '.join(unmatched) or 'nothing given'}")
    if not (reason or "").strip():
        raise ExceptionError("a suppression needs a reason")
    a, b = (datetime.fromisoformat(x) for x in (start, end))
    a, b = (x if x.tzinfo else x.replace(tzinfo=UTC) for x in (a, b))
    if b <= a:
        raise ExceptionError("a suppression ends after it starts")
    return conn.execute("insert into suppression (subjects, start_at, end_at, reason, created_by) "
                        "values (%s, %s, %s, %s, %s) returning *", (subjects, a, b, reason.strip(), by)).fetchone()


def end(conn, suppression_id: int, by: str) -> dict:
    """End a person's window now; what it held back stays recorded."""
    row = conn.execute("update suppression set end_at = greatest(start_at + interval '1 second', now()) "
                       "where id = %s and end_at > now() returning *", (suppression_id,)).fetchone()
    if row is None:
        raise ExceptionError(f"no running suppression {suppression_id}")
    return row


def windows(conn) -> list[dict]:
    """Every window running now that holds something back, or a person's, with what it has suppressed."""
    return conn.execute(
        """
        with w as (
            select case when needs_shutdown or kind in ('turnaround', 'shutdown') then 'shutdown' else 'planned_work' end as kind,
                   id, title as reason, start_at, end_at, created_by from plan_item
            where status = any(%s) and start_at <= now() and end_at > now()
            union all
            select 'person', id, reason, start_at, end_at, created_by from suppression where end_at > now())
        select w.*, coalesce(sum(f.raised), 0)::int as raised, count(f.id)::int as findings
        from w left join suppressed_finding f on f.window_kind = w.kind and f.window_id = w.id
        group by w.kind, w.id, w.reason, w.start_at, w.end_at, w.created_by
        having w.kind = 'person' or count(f.id) > 0 order by w.end_at
        """, (list(PLAN_LIVE),)).fetchall()


def found(conn, limit: int = 200) -> dict:
    """What suppression held back, newest first, and how much: counted, not lost."""
    rows = conn.execute("select id, type, key, title, source, subjects, value_usd, window_kind, window_id, reason, "
                        "raised, first_seen, last_seen from suppressed_finding order by last_seen desc limit %s",
                        (limit,)).fetchall()
    n = conn.execute("select count(*)::int as findings, coalesce(sum(raised), 0)::int as raised "
                     "from suppressed_finding").fetchone()
    return {**n, "items": rows}

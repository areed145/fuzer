"""Shift handover for the IOC (#467): at each shift change, what the incoming ops lead needs to know in a few minutes,
drafted from live work, every line citing its case or exception the way the daily report cites its records.

Six sections, over the shift that just ended:

    Open work               open cases by priority (most urgent first)
    In the field            jobs in progress (on site, not yet done) and with whom, and handovers waiting to be
                            accepted (P5.11d)
    Open permits            permits to work not closed or expired, and isolations not yet in place (P5.11d)
    Back from the field     exceptions the field reported done or could not do, waiting on the IOC to decide
    Escalated or overdue    steps escalated during the shift, and steps past their due time now
    Dispatched              what the outgoing shift sent to the field, and to whom
    New exceptions          exceptions raised during the shift
    Records to check        downtime with no log entry or notification, and logged stops with no downtime record
                            (#344): questions for the shift, open until a matching record turns up or it is closed

Shift changes are the `handover.shift_times` setting (local times in the project's timezone, "06:00,18:00"). The
work tick drafts a handover once per shift change (`at_shift_change`); it starts the `shift_handover` process, one
step on the ops lead queue with its SLA (#509). The outgoing lead adds notes; the incoming lead acknowledges it, which is kept on the case history
(who and when) and closes the case. Past handovers stay readable.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..core import settings, work
from ..core.exceptions import ExceptionError

SECTIONS = ["Open work", "In the field", "Open permits", "Rounds", "Back from the field", "Escalated or overdue", "Dispatched",
            "New exceptions", "Records to check"]
RECORDS = "records_gap"  # its own section, so not repeated under Open work and New exceptions
CASE_TYPE = "shift_handover"
ROLE = "ops_lead"
LIMIT = 25  # lines per section; the rest is counted


def _tz() -> ZoneInfo:
    from ..core import projects

    try:
        return ZoneInfo(projects.field("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def shift_times(conn) -> list[time]:
    raw = settings.get(conn, "handover.shift_times") or "06:00,18:00"
    out = []
    for part in str(raw).split(","):
        h, _, m = part.strip().partition(":")
        out.append(time(int(h), int(m or 0)))
    return sorted(set(out))


def boundaries(conn, now: datetime) -> tuple[datetime, datetime]:
    """The shift that just ended: (its start, its end = the latest shift change at or before `now`)."""
    tz = _tz()
    local = now.astimezone(tz)
    times = shift_times(conn)
    changes = sorted(datetime.combine(local.date() + timedelta(days=d), t, tz) for d in (-2, -1, 0) for t in times)
    past = [c for c in changes if c <= local]
    return past[-2].astimezone(UTC), past[-1].astimezone(UTC)


def _line(text: str, *cites: str) -> dict:
    return {"text": text, "cites": [c for c in cites if c]}


def facts(conn, start: datetime, end: datetime) -> dict:
    """The sections over [start, end), from live work. Each line is {text, cites}; `more` counts the rest."""
    sections: dict[str, list[dict]] = {s: [] for s in SECTIONS}
    more: dict[str, int] = {}

    def keep(name: str, rows: list, render) -> None:
        sections[name] = [render(r) for r in rows[:LIMIT]]
        if len(rows) > LIMIT:
            more[name] = len(rows) - LIMIT

    holder = lambda r: r["assignee"] or (f"the {r['role'].replace('_', ' ')} queue" if r["role"] else "nobody yet")
    keep("Open work", conn.execute(
        """
        select id, title, type, priority, assignee, role, due from work_item
        where state not in ('done', 'cancelled') and type <> all(%s)
        order by priority desc nulls last, due nulls last, created_at
        """, ([CASE_TYPE, RECORDS],)).fetchall(),
        lambda r: _line(f"{r['title']} ({r['type'].replace('_', ' ')}), with {holder(r)}"
                        + (f", due {r['due']:%d %b %H:%M}" if r["due"] else ""), f"case:{r['id']}"))
    keep("In the field", conn.execute(
        """
        select e.id, e.title, e.item_id, f->>'by' as by, f->>'at' as at, e.evidence->'handover' as handover
        from exception_record e, lateral (select evidence->'field'->-1 as f) last
        where e.state = 'open' and e.joined_to is null and f->>'status' = 'on_site'
        order by (f->>'at')
        """).fetchall(),
        lambda r: _line(f"{r['title']}: {r['by']} on site since {_local(r['at'])}"
                        + (f"; handed over to {r['handover']['to']}, waiting to be accepted"
                           + (f" (unsafe to leave: {r['handover']['unsafe']})" if r["handover"].get("unsafe") else "")
                           if r["handover"] and not r["handover"].get("accepted_at") else ""),
                        f"exception:{r['id']}", f"case:{r['item_id']}" if r["item_id"] else ""))
    keep("Open permits", _permits(conn, end), lambda r: _line(r["text"], f"entity:{r['id']}"))
    from . import rounds

    keep("Rounds", rounds.of_shift(conn, start, end), lambda r: _line(r["text"]))
    keep("Back from the field", conn.execute(
        """
        select e.id, e.title, e.item_id, f->>'status' as said, f->>'by' as by, f->>'note' as note
        from exception_record e, lateral (select evidence->'field'->-1 as f) last
        where e.state = 'open' and e.joined_to is null and f->>'status' in ('done', 'could_not_do')
        order by (f->>'at') desc
        """).fetchall(),
        lambda r: _line(f"{r['title']}: {r['by']} reported {'done' if r['said'] == 'done' else 'could not do'}"
                        + (f" ({r['note']})" if r["note"] else ""), f"exception:{r['id']}",
                        f"case:{r['item_id']}" if r["item_id"] else ""))
    keep("Escalated or overdue", conn.execute(
        """
        select t.id, t.item_id, coalesce(t.name, t.element) as step, i.title, t.assignee, t.role, t.due_at,
               t.escalated_at
        from work_token t join work_item i on i.id = t.item_id
        where t.kind = 'user' and ((t.escalated_at >= %(s)s and t.escalated_at < %(e)s)
                                   or (t.state = 'waiting' and t.due_at < %(e)s))
        order by coalesce(t.escalated_at, t.due_at)
        """, {"s": start, "e": end}).fetchall(),
        lambda r: _line(f"{r['step']} on {r['title']}, with {holder(r)}: "
                        + (f"escalated {r['escalated_at']:%H:%M}" if r["escalated_at"] else
                           f"overdue since {r['due_at']:%d %b %H:%M}"), f"case:{r['item_id']}"))
    keep("Dispatched", conn.execute(
        """
        select o.created_at, o.payload, o.ref from event_outbox o
        where o.topic = 'exception.dispatched' and o.created_at >= %(s)s and o.created_at < %(e)s order by o.id
        """, {"s": start, "e": end}).fetchall(),
        lambda r: _line(f"{r['payload'].get('title')} sent to {str(r['payload'].get('to') or '').removeprefix('role:')}"
                        f" at {r['created_at']:%H:%M}" + (f" ({r['payload']['reason']})" if r["payload"].get("reason") else ""),
                        r["ref"], f"case:{r['payload']['item_id']}" if r["payload"].get("item_id") else ""))
    keep("New exceptions", conn.execute(
        """
        select id, title, type, source, item_id, state from exception_record
        where first_seen >= %(s)s and first_seen < %(e)s and joined_to is null and type <> %(r)s
        order by value_usd desc nulls last, id
        """, {"s": start, "e": end, "r": RECORDS}).fetchall(),
        lambda r: _line(f"{r['title']} ({r['type'].replace('_', ' ')} from {r['source']}, {r['state']})",
                        f"exception:{r['id']}", f"case:{r['item_id']}" if r["item_id"] else ""))
    keep("Records to check", conn.execute(
        """
        select id, title, item_id, evidence->>'at' as at from exception_record
        where type = %s and state = 'open' and joined_to is null order by evidence->>'at' desc, id
        """, (RECORDS,)).fetchall(),
        lambda r: _line(r["title"], f"exception:{r['id']}", f"case:{r['item_id']}" if r["item_id"] else ""))
    return {"sections": sections, "more": more}


def _local(at: str | None) -> str:
    if not at:
        return "?"
    try:
        return datetime.fromisoformat(at).astimezone(_tz()).strftime("%H:%M")
    except ValueError:
        return at[11:16]


def _permits(conn, end: datetime) -> list[dict]:
    """Permits to work not closed or expired at the shift change, with any isolation not yet in place, from the
    permit records (P5.11d)."""
    from .readiness import IN_PLACE, LIVE, _when

    rows = conn.execute(
        """
        select p.fuzer_id as id, pl.label, ll.label as location,
               max(case when g.field = 'permit_type' then g.value #>> '{}' end) as type,
               max(case when g.field = 'state' then g.value #>> '{}' end) as state,
               max(case when g.field = 'valid_to' then g.value #>> '{}' end) as valid_to,
               (select coalesce(jsonb_agg(jsonb_build_object('isolation', il.label, 'point', ip.value #>> '{}',
                                                             'state', ist.value #>> '{}') order by il.label), '[]')
                from edge i join entity_label il on il.fuzer_id = i.src
                left join golden ip on ip.fuzer_id = i.src and ip.field = 'point'
                left join golden ist on ist.fuzer_id = i.src and ist.field = 'state'
                where i.rel = 'isolation_for' and i.dst = p.fuzer_id) as isolations
        from entity p left join entity_label pl on pl.fuzer_id = p.fuzer_id
        left join edge c on c.src = p.fuzer_id and c.rel = 'covers'
        left join entity_label ll on ll.fuzer_id = c.dst
        left join golden g on g.fuzer_id = p.fuzer_id
        where p.kind = 'permit'
        group by p.fuzer_id, pl.label, ll.label
        order by pl.label
        """).fetchall()
    out = []
    for r in rows:
        until = _when(r["valid_to"])
        if (r["state"] or "").lower() not in (*LIVE, "approved", "suspended") or (until and until < end):
            continue
        waiting = [i for i in r["isolations"] if (i["state"] or "").lower() not in IN_PLACE]
        text = (f"{r['label']} {r['type'] or 'permit'} on {r['location'] or '?'}, {r['state']}"
                + (f" to {r['valid_to']}" if r["valid_to"] else "")
                + (f"; {len(waiting)} of {len(r['isolations'])} isolations not in place ("
                   + ", ".join(f"{i['isolation']} {i['state'] or 'no state'}" for i in waiting) + ")" if waiting
                   else f"; {len(r['isolations'])} isolation{'s' if len(r['isolations']) > 1 else ''} in place"
                   if r["isolations"] else ""))
        out.append({"id": r["id"], "text": text})
    return out


def draft(conn, by: str, now: datetime | None = None, force: bool = False) -> dict | None:
    """The handover for the shift change just passed, drafted from live work, with its case on the ops lead queue.
    None when it was already drafted (unless `force`, which refreshes the draft while nobody has acknowledged it)."""
    now = now or datetime.now(UTC)
    start, end = boundaries(conn, now)
    have = conn.execute("select * from shift_handover where shift_end = %s", (end,)).fetchone()
    if have and not (force and have["state"] != "acknowledged"):
        return None
    f = facts(conn, start, end)
    if have:
        return _ordered(conn.execute("update shift_handover set facts = %s, drafted_at = now() where id = %s returning *",
                                 (Jsonb(f), have["id"])).fetchone())
    row = dict(conn.execute(
        "insert into shift_handover (shift_start, shift_end, facts, created_by) values (%s, %s, %s, %s) returning *",
        (start, end, Jsonb(f), by)).fetchone())
    label = end.astimezone(_tz()).strftime("%a %d %b %H:%M")
    n = {k: len(v) + f["more"].get(k, 0) for k, v in f["sections"].items()}
    from . import routing

    payload = {"handover_id": row["id"], **{k.lower(): v for k, v in n.items()}}
    item_id = routing.open_process_case(conn, CASE_TYPE, {**payload, "source": "handover"}, by,
                                        f"Shift handover {label}", priority=1.0,
                                        plain={"role": ROLE, "source": "handover", "payload": payload})
    return _ordered(conn.execute("update shift_handover set item_id = %s where id = %s returning *",
                             (item_id, row["id"])).fetchone())


def at_shift_change(conn, now: datetime | None = None) -> dict | None:
    """The work tick: draft the handover once a shift change has passed (off when the setting is empty)."""
    if not settings.get(conn, "handover.shift_times"):
        return None
    return draft(conn, "handover", now)


def _ordered(row) -> dict:
    """jsonb keeps keys in its own order: the sections back in the handover's."""
    row = dict(row)
    sec = (row.get("facts") or {}).get("sections") or {}
    row["facts"] = {**row["facts"], "sections": {k: sec.get(k, []) for k in SECTIONS}}
    return row


def get(conn, handover_id: int) -> dict:
    r = conn.execute("select * from shift_handover where id = %s", (handover_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no handover {handover_id}")
    return _ordered(r)


def listing(conn, limit: int = 60) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select id, shift_start, shift_end, state, notes_by, acknowledged_by, acknowledged_at, item_id, "
        "(select sum(jsonb_array_length(v)) from jsonb_each(facts->'sections') s(k, v)) as lines "
        "from shift_handover order by shift_end desc limit %s", (limit,))]


def add_notes(conn, handover_id: int, by: str, notes: str) -> dict:
    """The outgoing lead's notes (what the lines don't say). Kept on the case history too."""
    h = get(conn, handover_id)
    if h["state"] == "acknowledged":
        raise ExceptionError("this handover was acknowledged; add a comment on its case instead")
    row = _ordered(conn.execute("update shift_handover set notes = %s, notes_by = %s, notes_at = now(), "
                            "state = 'handed_over' where id = %s returning *", (notes.strip(), by, handover_id)).fetchone())
    if h["item_id"]:
        work.comment(conn, h["item_id"], by, f"Outgoing notes: {notes.strip()}")
    return row


def acknowledge(conn, handover_id: int, by: str, note: str | None = None) -> dict:
    """The incoming lead has read it: who and when go on the record, and its case closes."""
    h = get(conn, handover_id)
    if h["state"] == "acknowledged":
        raise ExceptionError(f"already acknowledged by {h['acknowledged_by']}")
    row = _ordered(conn.execute("update shift_handover set state = 'acknowledged', acknowledged_by = %s, "
                            "acknowledged_at = now(), acknowledged_note = %s where id = %s returning *",
                            (by, (note or "").strip() or None, handover_id)).fetchone())
    if h["item_id"]:
        work.comment(conn, h["item_id"], by, "Acknowledged the handover" + (f": {note.strip()}" if note else ""))
        work.close_case(conn, h["item_id"], "done", by, reason="handover acknowledged")
    return row

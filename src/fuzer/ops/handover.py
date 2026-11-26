"""Shift handover for the IOC (#467): at each shift change, what the incoming ops lead needs to know in a few minutes,
drafted from live work, every line citing its case or exception the way the daily report cites its records.

The sections, over the shift that just ended:

    In the field            jobs in progress (on site, not yet done) and with whom, and handovers waiting to be
                            accepted (P5.11d)
    Open permits            permits to work not closed or expired, and isolations not yet in place (P5.11d)
    Active bypasses         safeguards out of service at the shift change, from the bypass register: what is
                            bypassed, on what, who approved it and when it must come out, overdue ones first (P5.11e)
    Back from the field     exceptions the field reported done or could not do, waiting on the IOC to decide
    Escalated or overdue    steps escalated during the shift, and steps past their due time now
    Dispatched              what the outgoing shift sent to the field, and to whom
    New exceptions          exceptions raised during the shift, less source problems that already cleared
    Closed                  cases closed during the shift
    Records to check        downtime with no log entry or notification, and logged stops with no downtime record
                            (#344): questions for the shift, open until a matching record turns up or it is closed

What changed this shift, not every open line (#1351): the standing backlog is All work's, where the page links.

A handover reads in under three phone screens (P5.26i, #1371): the same line said again (a round done, a dispatch of one
title) is one line with how many times, and a section's `links` names the List filtered to its rows where the List can
show the same rows, so the page lists the first few and links the rest. A line's time is its `at` (an instant, or the plant's
clock), written `{at}` in its text, so the page says it with its date in the reader's clock: a time written "08:10" on
the day of the shift change read without a day the next morning, and a running shift's lines had no day at all.

Shift changes are the `handover.shift_times` setting (local times in the project's timezone, "06:00,18:00"). The
work tick drafts a handover once per shift change (`at_shift_change`); it starts the `shift_handover` process, one
step on the ops lead queue with its SLA (#509). The outgoing lead adds notes, which changes nothing else; the incoming lead
acknowledges it, which is kept on the case history (who and when) and closes the case. Past handovers stay readable,
each with its `status`: in progress, acknowledged, or not acknowledged.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..core import settings, wording, work
from ..core.exceptions import ExceptionError

SECTIONS = ["In the field", "Open permits", "Active bypasses", "Rounds", "Back from the field", "Escalated or overdue", "Dispatched",
            "New exceptions", "Closed", "Records to check"]
RECORDS = "records_gap"  # its own section, so not repeated under Open work and New exceptions
ROUTINE = "routine_due"  # planned work coming due, not news: on Plan and All work, never under New exceptions
CASE_TYPE = "shift_handover"
ROLE = "ops_lead"
LIMIT = 25  # lines per section; the rest is counted
# The List filtered to a section's rows, where it can show the same rows: the page links "N more" there.
# Those are the List's own rows, one line each, so their counts stay the List's: their lines are never folded together.
LINKS = {"In the field": "/list?stage=field", "Back from the field": "/list?stage=back",
         "Records to check": f"/list?kind={RECORDS}"}


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


def _changes(conn, now: datetime) -> tuple[list[datetime], list[datetime]]:
    """Shift changes around `now`: those at or before it, and those after it."""
    tz = _tz()
    local = now.astimezone(tz)
    times = shift_times(conn)
    changes = sorted(datetime.combine(local.date() + timedelta(days=d), t, tz) for d in (-2, -1, 0, 1) for t in times)
    return [c for c in changes if c <= local], [c for c in changes if c > local]


def boundaries(conn, now: datetime) -> tuple[datetime, datetime]:
    """The shift that just ended: (its start, its end = the latest shift change at or before `now`)."""
    past, _ = _changes(conn, now)
    return past[-2].astimezone(UTC), past[-1].astimezone(UTC)


def current_shift(conn, now: datetime) -> tuple[datetime, datetime]:
    """The shift in progress: (the latest shift change at or before `now`, the next one)."""
    past, ahead = _changes(conn, now)
    return past[-1].astimezone(UTC), ahead[0].astimezone(UTC)


def _at(t: datetime | str | None) -> str | None:
    """A line's time as the page reads it: an instant in ISO with its offset, or the plant's clock as written."""
    if not t:
        return None
    return t.isoformat(timespec="minutes") if isinstance(t, datetime) else str(t)


def _line(text: str, *cites: str, at: datetime | str | None = None) -> dict:
    """A line: its words (`{at}` where its time goes), what it cites, and its time."""
    line = {"text": wording.say(text), "cites": [c for c in cites if c]}
    return {**line, "at": _at(at)} if at else line


def _once(lines: list[dict]) -> list[dict]:
    """The same words said again become one line with how many times, at the first's place and with its citations."""
    seen: dict[str, dict] = {}
    for line in lines:
        key = line["text"]
        if key in seen:
            seen[key]["times"] = seen[key].get("times", 1) + 1
        else:
            seen[key] = dict(line)
    out = []
    for x in seen.values():
        times = x.pop("times", 1)
        out.append({**x, "text": f"{x['text']} ({times} times)"} if times > 1 else x)
    return out


def _to(to: str | None) -> str:
    """Who work was sent to: a person by name, a role as its queue."""
    to = str(to or "")
    return f"the {wording.queue(to)}" if to.startswith("role:") else to or "nobody"


def readable(facts: dict) -> dict:
    """A drafted handover's lines in people's words (#1072), for handovers drafted before the wording layer."""
    return {**facts, "sections": {name: [{**line, "text": wording.say(line.get("text"))} for line in lines]
                                  for name, lines in (facts.get("sections") or {}).items()}}


def facts(conn, start: datetime, end: datetime) -> dict:
    """The sections over [start, end), what changed in it, from live work. Each line is {text, cites}; `more` counts
    the rest; `unknown` names a section that can't be said and why (no bypass register loaded), which is not a section
    with nothing."""
    from ..sources import alerts
    from . import exceptions, safeguards

    kinds = {k: t.label for k, t in exceptions.types(conn).items()}
    sections: dict[str, list[dict]] = {s: [] for s in SECTIONS}
    more: dict[str, int] = {}
    unknown = {} if safeguards.loaded(conn) else {"Active bypasses": safeguards.NO_REGISTER}

    def keep(name: str, rows: list, render) -> None:
        lines = [render(r) for r in rows]
        lines = lines if name in LINKS else _once(lines)
        sections[name] = lines[:LIMIT]
        if len(lines) > LIMIT:
            more[name] = len(lines) - LIMIT

    holder = lambda r: r.get("assignee") or (f"the {wording.queue(r['role'])}" if r.get("role") else "nobody yet")
    # In the field and Back from the field are the List's own rows (ops.worklist), so the handover's counts are the
    # List's and the Board's (red team #4): the same open work, the same stages. The rest of the open work is not
    # listed (#1351): it was 359 lines a shift, the same each time; All work has it.
    from . import worklist

    live = [r for r in worklist._all(conn, "open") if r["kind"] not in (CASE_TYPE, RECORDS)]
    cite = lambda r: (f"exception:{r['exception_id']}" if r.get("exception_id") else "", f"case:{r['item_id']}" if r.get("item_id") else "")
    last = lambda r: ((r.get("evidence") or {}).get("field") or [None])[-1] if isinstance(r.get("evidence"), dict) else None
    def in_field(r) -> dict:
        said, handed = last(r), (r.get("evidence") or {}).get("handover")
        on_site = said and said.get("status") == "on_site"
        text = f"{r['title']}: {said['by']} on site since {{at}}" if on_site else f"{r['title']}: with {holder(r)}"
        if handed and not handed.get("accepted_at"):
            text += f"; handed over to {handed['to']}, waiting to be accepted" + (
                f" (unsafe to leave: {handed['unsafe']})" if handed.get("unsafe") else "")
        return _line(text, *cite(r), at=said.get("at") if on_site else None)
    keep("In the field", sorted((r for r in live if r["stage"] == "field"), key=lambda r: (last(r) or {}).get("at") or "~"), in_field)

    def came_back(r) -> dict:
        said = last(r)
        if not said or said.get("status") not in ("done", "could_not_do"):
            return _line(f"{r['title']}: back with {holder(r)}", *cite(r))
        return _line(f"{r['title']}: {said['by']} reported {'done' if said['status'] == 'done' else 'could not do'}"
                     + (f" ({said['note']})" if said.get("note") else ""), *cite(r))
    back = sorted((r for r in live if r["stage"] == "back"), key=lambda r: (last(r) or {}).get("at") or "", reverse=True)
    keep("Open permits", _permits(conn, end), lambda r: _line(r["text"], f"entity:{r['id']}", at=r.get("at")))
    keep("Active bypasses", _bypasses(conn, end), lambda r: _line(r["text"], f"entity:{r['id']}", at=r.get("at")))
    from . import rounds

    # rounds done with nothing found are one line; a round that found something, or was partly done or missed, its own
    ran = rounds.of_shift(conn, start, end)
    clean = [r for r in ran if r["state"] == "done" and not r["text"].endswith("found something")]
    keep("Rounds", ran if len(clean) < 2 else [r for r in ran if r not in clean]
         + [{"text": f"{len(clean)} rounds done, nothing found"}], lambda r: _line(r["text"]))
    keep("Back from the field", back, came_back)
    keep("Escalated or overdue", conn.execute(
        """
        select t.id, t.item_id, coalesce(t.name, t.element) as step, i.title, t.assignee, t.role, t.due_at,
               t.escalated_at
        from work_token t join work_item i on i.id = t.item_id
        where t.kind = 'user' and ((t.escalated_at >= %(s)s and t.escalated_at < %(e)s)
                                   or (t.state = 'waiting' and t.due_at >= %(s)s and t.due_at < %(e)s))
        -- what went late this shift; work late for days is under Open work, not leading every handover
        order by coalesce(t.escalated_at, t.due_at)
        """, {"s": start, "e": end}).fetchall(),
        lambda r: _line(f"{r['step']} on {r['title']}, with {holder(r)}: "
                        + ("escalated {at}" if r["escalated_at"] else "overdue since {at}"), f"case:{r['item_id']}",
                        at=r["escalated_at"] or r["due_at"]))
    keep("Dispatched", conn.execute(
        """
        select o.created_at, o.payload, o.ref from event_outbox o
        where o.topic = 'exception.dispatched' and o.created_at >= %(s)s and o.created_at < %(e)s order by o.id
        """, {"s": start, "e": end}).fetchall(),
        lambda r: _line(f"{wording.say(r['payload'].get('title'))} sent to {_to(r['payload'].get('to'))} {{at}}"
                        + (f" ({r['payload']['reason']})" if r["payload"].get("reason") else ""),
                        r["ref"], f"case:{r['payload']['item_id']}" if r["payload"].get("item_id") else "",
                        at=r["created_at"]))
    keep("New exceptions", conn.execute(
        """
        select id, title, type, source, item_id, state from exception_record
        where first_seen >= %(s)s and first_seen < %(e)s and joined_to is null and type <> all(%(r)s)
          and not (type = %(src)s and state = 'cleared')  -- a load that failed and then loaded is not the next shift's
        order by value_usd desc nulls last, id
        """, {"s": start, "e": end, "r": [RECORDS, ROUTINE], "src": alerts.TYPE}).fetchall(),
        # the kind by its label and the source in words, as the case says them: "Field report from Demo crew", not
        # "field report from sample crew" (tour D15)
        lambda r: _line(f"{r['title']} ({kinds.get(r['type'], r['type'].replace('_', ' '))} from "
                        f"{wording.source(r['source'])}, {r['state']})",
                        f"exception:{r['id']}", f"case:{r['item_id']}" if r["item_id"] else ""))
    keep("Closed", conn.execute(
        """
        select w.id, w.title, w.state, w.closed_at from work_item w
        where w.closed_at >= %(s)s and w.closed_at < %(e)s and w.state in ('done', 'cancelled') and w.type <> %(h)s
          and not exists (select 1 from exception_record x where x.item_id = w.id and x.type = %(src)s
                          and x.state = 'cleared')
        order by w.closed_at desc, w.id
        """, {"s": start, "e": end, "h": CASE_TYPE, "src": alerts.TYPE}).fetchall(),
        lambda r: _line(f"{r['title']}: {'done' if r['state'] == 'done' else 'cancelled'} {{at}}",
                        f"case:{r['id']}", at=r["closed_at"]))
    keep("Records to check", conn.execute(
        """
        select id, title, item_id, evidence->>'at' as at from exception_record
        where type = %s and state = 'open' and joined_to is null order by evidence->>'at' desc, id
        """, (RECORDS,)).fetchall(),
        lambda r: _line(r["title"], f"exception:{r['id']}", f"case:{r['item_id']}" if r["item_id"] else ""))
    return {"sections": sections, "more": more, "unknown": unknown,
            "links": {k: v for k, v in LINKS.items() if sections.get(k)}}


def _bypasses(conn, end: datetime) -> list[dict]:
    """Safeguards bypassed at the shift change, from the bypass register (P3.9, P5.11e): an overdue one first and
    saying so, a removed or not yet started one left out. Without a bypass register there are no lines, and the
    section is unknown (facts' `unknown`), not empty."""
    from . import safeguards

    rows = sorted(safeguards.bypasses(conn, at_time=end), key=lambda b: (not b["overdue"], b["expires"] or "", b["label"] or ""))
    out = []
    for b in rows:
        what = b.get("function") or "safeguard"
        on = ", ".join(b["protects_labels"]) or ", ".join(b["acts_on_labels"]) or "?"
        until = safeguards._when(b["expires"])
        text = (f"{b['label']}: {what} ({b['safeguard']}) bypassed on {on}"
                + (f", approved by {b['authorised_by']}" if b.get("authorised_by") else ", no approver on record")
                + ((", overdue since {at}" if b["overdue"] else ", expires {at}") if until else ", no expiry on record")
                + (f", under {b['moc']}" if b.get("moc") else ""))
        out.append({"id": b["id"], "text": text, "at": until})
    return out


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
                + (" to {at}" if r["valid_to"] else "")
                + (f"; {len(waiting)} of {len(r['isolations'])} isolations not in place ("
                   + ", ".join(f"{i['isolation']} {i['state'] or 'no state'}" for i in waiting) + ")" if waiting
                   else f"; {len(r['isolations'])} isolation{'s' if len(r['isolations']) > 1 else ''} in place"
                   if r["isolations"] else ""))
        out.append({"id": r["id"], "text": text, "waiting": bool(waiting), "at": r["valid_to"]})
    return sorted(out, key=lambda r: not r["waiting"])  # plant not yet isolated under a live permit leads


def draft(conn, by: str, now: datetime | None = None, force: bool = False, current: bool = False) -> dict | None:
    """The handover for the shift change just passed, drafted from live work, with its case on the ops lead queue.
    None when it was already drafted (unless `force`, which refreshes the draft while nobody has acknowledged it).

    `current` drafts the shift in progress instead, so the outgoing lead can write it before they go: its lines run
    to now and it has no case yet. At the shift change the work tick brings that same handover up to date, keeping
    the notes, and opens its case for the incoming lead. A handover whose shift has ended is never redrafted by hand:
    it says what held at its shift change."""
    now = now or datetime.now(UTC)
    start, end = current_shift(conn, now) if current else boundaries(conn, now)
    have = conn.execute("select * from shift_handover where shift_end = %s", (end,)).fetchone()
    if have and (have["state"] == "acknowledged" or (have["item_id"] and not force) or (have["item_id"] and not current)):
        return None
    f = facts(conn, start, min(end, now))
    if have:
        row = dict(conn.execute("update shift_handover set facts = %s, drafted_at = now() where id = %s returning *",
                                (Jsonb(f), have["id"])).fetchone())
    else:
        row = dict(conn.execute(
            "insert into shift_handover (shift_start, shift_end, facts, created_by) values (%s, %s, %s, %s) returning *",
            (start, end, Jsonb(f), by)).fetchone())
    if current or row["item_id"]:
        return _ordered(row)
    label = end.astimezone(_tz()).strftime("%b %-d, %-I:%M %p")
    n = {k: None if k in f["unknown"] else len(v) + f["more"].get(k, 0) for k, v in f["sections"].items()}
    from . import routing

    payload = {"handover_id": row["id"], **{k.lower(): v for k, v in n.items()}}
    _supersede(conn, end, label, by)
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


def status(row, now: datetime | None = None) -> str:
    """Where a handover stands, in the words the page shows (#1351): `in progress` while its shift runs, then
    `acknowledged` or `not acknowledged`. A past one nobody read is not a draft; notes don't move it."""
    if row["state"] == "acknowledged":
        return "acknowledged"
    return "in progress" if row["shift_end"] > (now or datetime.now(UTC)) else "not acknowledged"


def _ordered(row) -> dict:
    """jsonb keeps keys in its own order: the sections back in the handover's (one no longer drafted, such as Open
    work in a handover drafted before #1351, stays readable after them)."""
    row = dict(row)
    sec = (row.get("facts") or {}).get("sections") or {}
    row["facts"] = readable({**row["facts"], "sections": {**{k: sec.get(k, []) for k in SECTIONS}, **sec}})
    return {**row, "status": status(row)}


def get(conn, handover_id: int) -> dict:
    r = conn.execute("select * from shift_handover where id = %s", (handover_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no handover {handover_id}")
    return _ordered(r)


def listing(conn, limit: int = 60) -> list[dict]:
    return [{**r, "status": status(r)} for r in conn.execute(
        "select id, shift_start, shift_end, state, notes_by, acknowledged_by, acknowledged_at, item_id, "
        "(select sum(jsonb_array_length(v)) from jsonb_each(facts->'sections') s(k, v)) as lines "
        "from shift_handover order by shift_end desc limit %s", (limit,))]


def add_notes(conn, handover_id: int, by: str, notes: str) -> dict:
    """The outgoing lead's notes (what the lines don't say). Kept on the case history too. Saving them changes nothing
    else: a running shift stays in progress, and only acknowledging hands it over (#1351)."""
    h = get(conn, handover_id)
    if h["state"] == "acknowledged":
        raise ExceptionError("this handover was acknowledged; add a comment on its case instead")
    row = _ordered(conn.execute("update shift_handover set notes = %s, notes_by = %s, notes_at = now() "
                                "where id = %s returning *", (notes.strip(), by, handover_id)).fetchone())
    if h["item_id"]:
        work.comment(conn, h["item_id"], by, f"Outgoing notes: {notes.strip()}")
    return row


def _supersede(conn, end: datetime, label: str, by: str) -> None:
    """Earlier handovers nobody acknowledged stop asking to be read when the next one is drafted: each was drafted from
    the work live at its shift change, so the newest says what holds now. Left open, they piled up at the top of the
    ops lead's My work as escalated (the persona tour found five shifts' worth, every one overdue). Their records stay
    on the handover page; their cases close with why."""
    for old in conn.execute(
            "select h.item_id from shift_handover h join work_item w on w.id = h.item_id "
            "where h.shift_end < %s and h.state <> 'acknowledged' and w.state not in ('done', 'cancelled')",
            (end,)).fetchall():
        work.close_case(conn, old["item_id"], "cancelled", by,
                        reason=f"Superseded by the {label} handover, which says what holds now")


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

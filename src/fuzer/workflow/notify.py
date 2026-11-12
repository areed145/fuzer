"""Telling the assignee (P5.2, #604): when work moves to a person they are told, with a link to the case.

    scan(conn)                    read new work_event rows (by a cursor) and due-soon steps into notifications
    mine(conn, person)            a person's notifications, newest first, with how many are unread
    mark_read(conn, person, ids)  mark some (or all) read

What tells someone, read from the events every path that moves work already writes, so dispatch, hand-back, the
SLA tick and a process's own routing need not call this module:

    handed_off to a person        dispatched (or handed_back, when the person had handed it to whoever gives it back)
    step_started / created        assigned (a step or case that went straight to someone)
    escalated                     the step's new holder (else its new role's people) and whoever it was taken from
    due soon                      a person step due within work.due_soon_minutes (once per due time)

Someone away is told nothing: their delegate is told instead, naming who they cover, and with no delegate the work
went to its queue and nobody is. Moving work yourself tells nobody. Each notification is made once (its `key`).
"""

from datetime import datetime, timedelta

from ..core import settings
from .definitions import people, utcnow

KINDS = ("dispatched", "handed_back", "escalated", "assigned", "due_soon")
EVENTS = ("handed_off", "step_started", "created", "escalated")
BATCH = 500


def link(item_id: int | None) -> str:
    """The case's page, relative to the application (the UI's hash route)."""
    return f"#/cases/{item_id}" if item_id else "#/inbox"


def _person(to: str | None) -> str | None:
    """A person's name from a hand-off target (`role:x`, `agent:x` and queue phrases are not people)."""
    if not to or ":" in to and not to.startswith("user:") or to.startswith("the ") or to == "system":
        return None
    return to


def _who(actor: str) -> str:
    if actor.startswith("agent:"):
        return f"The {actor[6:]} agent"
    return "Fuzer" if actor == "system" else actor


def _title(conn, item_id: int | None, cache: dict) -> str:
    if item_id not in cache:
        row = conn.execute("select title from work_item where id = %s", (item_id,)).fetchone() if item_id else None
        cache[item_id] = row["title"] if row else "a case"
    return cache[item_id]


def _handed_back(conn, e: dict, person: str) -> bool:
    """Had `person` handed this step (or case) to whoever is now giving it back?"""
    scope = ("token_id = %(t)s", e["token_id"]) if e["token_id"] else ("item_id = %(t)s and token_id is null", e["item_id"])
    return conn.execute(
        f"select 1 from work_event where {scope[0]} and kind = 'handed_off' and id < %(id)s and actor = %(p)s "
        "and data->>'to' in (%(a)s, %(f)s) limit 1",
        {"t": scope[1], "id": e["id"], "p": person, "a": e["actor"], "f": e["data"].get("from") or ""}).fetchone() is not None


def _role_people(conn, role: str, now: datetime) -> list[str]:
    return [p["name"] for p in people(conn) if role in (p["roles"] or [])
            and not (p["away_until"] and p["away_until"] > now)]


def _recipients(conn, e: dict, title: str, now: datetime) -> list[tuple[str, str, str]]:
    """(person, kind, text) for one event."""
    d, actor, out = e["data"], e["actor"], []
    step = f" ({d['step']})" if d.get("step") else ""
    reason = f" {d['reason']}" if d.get("reason") else ""
    if e["kind"] == "handed_off" and (p := _person(d.get("to"))) and p != actor:
        if _handed_back(conn, e, p):
            out.append((p, "handed_back", f"{_who(actor)} handed back '{title}' to you.{reason}"))
        else:
            out.append((p, "dispatched", f"{_who(actor)} sent you '{title}'.{reason}"))
    elif e["kind"] in ("step_started", "created") and (p := _person(d.get("assignee"))) and p != actor:
        out.append((p, "assigned", f"'{title}'{step} is yours."))
    elif e["kind"] == "escalated":
        why = d.get("why") or "Its SLA passed."
        holder = conn.execute("select assignee, role from work_token where id = %s", (e["token_id"],)).fetchone()
        now_with = [holder["assignee"]] if holder and holder["assignee"] else (
            _role_people(conn, holder["role"], now) if holder and d.get("to", "").startswith("role:") else [])
        out += [(p, "escalated", f"'{title}' was escalated to you. {why}") for p in now_with]
        if (p := _person(d.get("from"))) and p not in now_with:
            out.append((p, "escalated", f"'{title}' was escalated away from you. {why}"))
    return out


def _add(conn, key: str, person: str, kind: str, item_id, token_id, text: str, now: datetime) -> int:
    """Make one notification, telling the delegate of someone away (nobody, without one). 1 when made."""
    from .roles import covering

    to = covering(conn, person, now)
    if to is None:
        return 0
    if to != person:
        text = f"{text} (You cover for {person}.)"
    row = conn.execute(
        "insert into work_notification (key, person, kind, item_id, token_id, text, link, created_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (key) do nothing returning id",
        (key, to, kind, item_id, token_id, text, link(item_id), now)).fetchone()
    return 1 if row else 0


def _due_soon(conn, now: datetime, cache: dict) -> int:
    minutes = settings.get(conn, "work.due_soon_minutes")
    if not minutes:
        return 0
    rows = conn.execute(
        "select t.id as token_id, t.item_id, t.assignee, t.due_at, t.name from work_token t where t.state = 'waiting' "
        "and t.kind = 'user' and t.assignee is not null and t.due_at > %(now)s and t.due_at <= %(until)s "
        "union all select null, i.id, i.assignee, i.due, null from work_item i where i.state not in ('done', 'cancelled') "
        "and i.assignee is not null and i.due > %(now)s and i.due <= %(until)s "
        "and not exists (select 1 from work_process p where p.item_id = i.id)",
        {"now": now, "until": now + timedelta(minutes=minutes)}).fetchall()
    made = 0
    for r in rows:
        title = _title(conn, r["item_id"], cache)
        mins = max(1, int((r["due_at"] - now).total_seconds() // 60))
        key = f"due:{r['token_id'] or 'i' + str(r['item_id'])}:{r['due_at'].isoformat()}:{r['assignee']}"
        made += _add(conn, key, r["assignee"], "due_soon", r["item_id"], r["token_id"],
                     f"'{title}' is due in {mins} min.", now)
    return made


def scan(conn, now: datetime | None = None) -> dict:
    """Make the notifications for work that moved since the last scan, and for steps coming due; commits. The first
    scan of a project starts from now, so history tells nobody."""
    now = now or utcnow()
    cur = conn.execute("select last_event from work_notify_cursor where id = 1 for update skip locked").fetchone()
    if cur is None and conn.execute("select 1 from work_notify_cursor").fetchone():
        conn.rollback()  # another scan is running: it makes them
        return {"events": 0, "notified": 0, "due_soon": 0}
    if cur is None:
        top = conn.execute("select coalesce(max(id), 0) as n from work_event").fetchone()["n"]
        conn.execute("insert into work_notify_cursor (id, last_event) values (1, %s) on conflict do nothing", (top,))
        cur = {"last_event": top}
    events = conn.execute("select * from work_event where id > %s and kind = any(%s) order by id limit %s",
                          (cur["last_event"], list(EVENTS), BATCH)).fetchall()
    top = events[-1]["id"] if events else cur["last_event"]
    made, cache = 0, {}
    for e in events:
        title = _title(conn, e["item_id"], cache)
        for person, kind, text in _recipients(conn, e, title, now):
            made += _add(conn, f"event:{e['id']}:{person}", person, kind, e["item_id"], e["token_id"], text, now)
    due = _due_soon(conn, now, cache)
    conn.execute("update work_notify_cursor set last_event = %s where id = 1", (top,))
    conn.commit()
    return {"events": len(events), "notified": made, "due_soon": due}


def mine(conn, person: str, limit: int = 30) -> dict:
    rows = conn.execute("select id, kind, item_id, token_id, text, link, channel, delivery, created_at, read_at "
                        "from work_notification where person = %s order by id desc limit %s", (person, limit)).fetchall()
    unread = conn.execute("select count(*) as n from work_notification where person = %s and read_at is null",
                          (person,)).fetchone()["n"]
    return {"notifications": rows, "unread": unread}


def mark_read(conn, person: str, ids: list[int] | None = None) -> dict:
    """Mark `ids` (all of the person's, when None) read; returns how many changed."""
    n = conn.execute("update work_notification set read_at = now() where person = %s and read_at is null "
                     "and (%s::bigint[] is null or id = any(%s::bigint[]))", (person, ids, ids)).rowcount
    conn.commit()
    return {"read": n}

"""The built-in exception implementation behind fuzer.core.exceptions: one `exception_record` per open condition,
matched to entities, routed to a case through the Work port, and published on the event outbox.

    raise_(conn, type, key, title, by, refs=..., priority=..., value_usd=..., evidence=..., source=..., assignee=...)
    clear(conn, type, key, by, reason)
    split(conn, exception_id, by)   take one that joined another's case back out to a case of its own
    reconcile(conn)          close the ones whose case finished (the work tick runs it)
    match(conn, refs)        -> (fuzer ids, refs that matched nothing)
    listing(conn, ...)       what the IOC (or the field) sees

States: open (the condition holds), cleared (it went away) and closed (its case finished while it held; the next
raise opens a new exception and a new case, so a closed case never hides a condition that came back).

One event, one case (#464): a new exception about the same asset as an open one from another source (the same
entity, or one sharing the equipment or unit it is part of), raised within `exceptions.join_hours` of it, joins that
one's case instead of opening another. The first is the host (`joined_to` null); the others point at it and share its
case, so the IOC triages, dispatches and closes once. Exceptions lists the host with every source under it.
"""

import functools
import re

from psycopg.types.json import Jsonb

from ..core import events, work
from ..core.exceptions import TYPES, ExceptionError, ExceptionType

OPEN = "open"


KIND_FIELDS = ("label", "role", "process", "case_type", "audience", "close_case_on_clear", "description", "joins")


def types(conn) -> dict[str, ExceptionType]:
    """Every exception type: the ones a deployment defined as data (exception_kind, for other systems' exceptions)
    and the ones modules declare in code, which win on a clash."""
    data = {r["name"]: ExceptionType(r["name"], r["label"], role=r["role"], process=r["process"],
                                     case_type=r["case_type"], audience=tuple(r["audience"]),
                                     close_case_on_clear=r["close_case_on_clear"], description=r["description"] or "",
                                     joins=r.get("joins", True))
            for r in conn.execute("select * from exception_kind")}
    return {**data, **TYPES}


def _type(conn, name: str) -> ExceptionType:
    t = TYPES.get(name) or types(conn).get(name)
    if t is None:
        raise ExceptionError(f"no exception type {name}; declare it with exception_type() or define it as data")
    return t


def match(conn, refs) -> tuple[list[str], list[str]]:
    """Resolve references to Fuzer IDs: a Fuzer ID itself, `kind:key` through entity_key, `source/key` through
    the crosswalk. Returns (matched ids in first-seen order, refs that matched nothing)."""
    ids, unmatched = [], []
    for ref in dict.fromkeys(r for r in refs or () if r):
        found = [r["fuzer_id"] for r in conn.execute(
            """
            select fuzer_id from entity where fuzer_id = %(r)s and retired_at is null
            union select fuzer_id from entity_key where kind || ':' || key = %(r)s
            union select fuzer_id from crosswalk where source || '/' || source_key = %(r)s
            """, {"r": ref})]
        if not found:  # a name a steward recorded for an entity (#476)
            from ..sources import aliases

            found = aliases.lookup(conn, ref)
        if found:
            ids += [f for f in sorted(found) if f not in ids]
        else:
            unmatched.append(ref)
    return ids, unmatched


def resolve_ref(conn, exception_id: int, ref: str, fuzer_id: str, by: str, remember: bool = True,
                reason: str | None = None) -> dict:
    """A person says which asset an unmatched ref meant (#476): it becomes a subject of the exception (and of its
    case), and with `remember` the ref's value is recorded as an alias, so the next one matches on its own. An
    exception no case works yet joins the open case about that asset, as one raised matched would have."""
    from ..sources import aliases

    row = _row(conn, exception_id, lock=True)
    if ref not in (row["unmatched"] or []):
        raise ExceptionError(f"{ref} is not an unmatched ref of exception {exception_id}")
    if not conn.execute("select 1 from entity where fuzer_id = %s and retired_at is null", (fuzer_id,)).fetchone():
        raise ExceptionError(f"no entity {fuzer_id}")
    subjects = list(dict.fromkeys([*row["subjects"], fuzer_id]))
    unmatched = [u for u in row["unmatched"] if u != ref]
    resolved = {"ref": ref, "fuzer_id": fuzer_id, "by": by, "remembered": remember,
                "at": conn.execute("select now() as t").fetchone()["t"].isoformat()}
    conn.execute("update exception_record set subjects = %s, unmatched = %s, evidence = jsonb_set(evidence, "
                 "'{resolved}', coalesce(evidence->'resolved', '[]') || %s), updated_at = now() where id = %s",
                 (subjects, unmatched, Jsonb([resolved]), exception_id))
    if remember:
        aliases.add(conn, ref, fuzer_id, by, source=row["source"], reason=reason or f"resolved on exception {exception_id}")
    if row["item_id"]:
        conn.execute("update work_item set subjects = (select array_agg(distinct s) from unnest(subjects || %s) s), "
                     "updated_at = now() where id = %s", ([fuzer_id], row["item_id"]))
        work.comment(conn, row["item_id"], by, f"Matched {ref} to {fuzer_id}" + (" (remembered)" if remember else ""))
    elif not row["joined_to"]:
        t = types(conn).get(row["type"])
        if t and (host := _host(conn, t, row["type"], row["key"], subjects)):
            conn.execute("update exception_record set joined_to = %s, item_id = %s where id = %s",
                         (host["id"], host["item_id"], exception_id))
            _joined(conn, t, _row(conn, exception_id), host, by, unmatched)
    row = _row(conn, exception_id)
    events.publish(conn, "exception.updated", f"exception:{row['id']}", {**_payload(row), "resolved": resolved})
    return row


def ref_forms(conn, per_kind: int = 1) -> list[dict]:
    """The reference forms `match` resolves, each with an example from this register, so another system knows what
    to send: a key kind (`tag:20-K-2100B`), a source's own key (`sap/10004711`), or a Fuzer ID. Most entities first."""
    keys = conn.execute(
        """
        select kind, count(distinct fuzer_id) as entities, min(kind || ':' || key) as example
        from entity_key where kind <> 'tag_noarea' group by kind order by count(distinct fuzer_id) desc, kind
        """).fetchall()
    sources = conn.execute(
        """
        select source || '/' as form, count(*) as entities, min(source || '/' || source_key) as example
        from crosswalk group by source order by count(*) desc, source
        """).fetchall()
    fid = conn.execute("select min(fuzer_id) as f, count(*) as n from entity where retired_at is null").fetchone()
    return ([{"form": f"{k['kind']}:<key>", "example": k["example"], "entities": k["entities"]} for k in keys]
            + [{"form": f"{r['form']}<source key>", "example": r["example"], "entities": r["entities"]} for r in sources]
            + ([{"form": "Fuzer ID", "example": fid["f"], "entities": fid["n"]}] if fid["f"] else []))


def _unmatched_note(conn, unmatched: list[str]) -> str | None:
    """What a sender is told when refs matched nothing: which, and the forms that would have, with examples."""
    if not unmatched:
        return None
    forms = ", ".join(f["example"] for f in ref_forms(conn)[:4])
    return (f"{len(unmatched)} ref{'s' if len(unmatched) > 1 else ''} matched nothing in the register "
            f"({', '.join(unmatched[:5])}); refs look like {forms or 'kind:key, source/key or a Fuzer ID'}")


def _payload(row: dict, t: ExceptionType | None = None) -> dict:
    keep = ("id", "type", "key", "title", "source", "state", "priority", "value_usd", "subjects", "unmatched",
            "item_id", "raised_count", "joined_to")
    out = {k: row.get(k) for k in keep}
    t = t or TYPES.get(row["type"])  # its declaring module may not be loaded in this process (the work tick)
    out["audience"] = list(t.audience) if t else []
    return out


def _case(conn, t: ExceptionType, row: dict, by: str, assignee: str | None, role: str | None) -> int | None:
    """Open the case that works it: start (or feed) the type's process, else a work item in its queue."""
    if t.process:
        data = {"exception_id": row["id"], "type": t.name, "key": row["key"], **row["evidence"]}
        return work.start_or_correlate(conn, t.process, data, by=by, title=row["title"], subjects=row["subjects"],
                                       priority=row["priority"])["item_id"]
    if not (assignee or role or t.role):
        return None
    queue = None if assignee else (role or t.role)
    return work.open_case(conn, t.case_type or t.name, row["title"], by, subjects=row["subjects"],
                          priority=row["priority"], assignee=assignee, role=queue.removeprefix("role:") if queue else None,
                          source=row["source"], payload={**row["evidence"], "exception_id": row["id"]})["id"]


def _close_if_case_finished(conn, row: dict | None) -> dict | None:
    if row and row["item_id"]:
        item = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()
        if item and item["state"] in ("done", "cancelled"):
            conn.execute("update exception_record set state = 'closed', closed_at = now(), updated_at = now() "
                         "where id = %s", (row["id"],))
            return None
    return row


def anchors(conn, ids: list[str]) -> set[str]:
    """What an exception's subjects stand for on the plant: each subject, the tag a point is the series of, the tag
    equipment is installed at, and what those are part of (a transmitter's compressor, a choke's well), up to three
    links. Areas and the facility are left out, or everything in one area would be one event."""
    if not ids:
        return set()
    return {r["id"] for r in conn.execute(
        """
        with recursive up (id, depth) as (
            select s, 0 from unnest(%s::text[]) s
            union
            select case when e.rel = 'has_timeseries' then e.src else e.dst end, up.depth + 1
            from up join edge e on (e.rel = 'has_timeseries' and e.dst = up.id)
                                or (e.rel in ('part_of', 'installed_at') and e.src = up.id and e.valid_to is null)
            where up.depth < 3)
        select distinct up.id from up join entity x on x.fuzer_id = up.id
        where x.kind not in ('area', 'facility', 'site', 'field')
        """, (list(ids),))}


def _host(conn, t: ExceptionType, type: str, key: str, subjects: list[str]) -> dict | None:
    """The open exception whose case a new one joins (#464): from another type or key, about an asset this one
    shares (see `anchors`), its case still open, seen within `exceptions.join_hours`. The most valuable, then the
    oldest. None when this type doesn't join, joining is off, or nothing matches."""
    from ..core import settings

    hours = settings.get(conn, "exceptions.join_hours")
    if not (t.joins and hours and subjects):
        return None
    mine = anchors(conn, subjects)
    joinable = [n for n, k in types(conn).items() if k.joins]
    for h in conn.execute(
        """
        select e.* from exception_record e join work_item w on w.id = e.item_id
        where e.state = 'open' and e.joined_to is null and w.state not in ('done', 'cancelled')
          and not (e.type = %s and e.key = %s) and e.type = any(%s) and cardinality(e.subjects) > 0
          and e.last_seen >= now() - make_interval(hours => %s)
        order by e.value_usd desc nulls last, e.first_seen
        """, (type, key, joinable, hours)).fetchall():
        if mine & anchors(conn, h["subjects"]):
            return h
    return None


def _adopt(conn, row: dict, by: str) -> int:
    """A case just opened for `row`: open exceptions about the same asset from other sources that no case works yet
    (an SAP notification waiting to be dispatched) join it, so the event is one row and one case (#464)."""
    from ..core import settings

    t = types(conn).get(row["type"])
    hours = settings.get(conn, "exceptions.join_hours")
    if not (row["item_id"] and hours and row["subjects"] and (t is None or t.joins)):
        return 0
    mine = anchors(conn, row["subjects"])
    joinable = [n for n, k in types(conn).items() if k.joins]
    n = 0
    for o in conn.execute(
        """
        select * from exception_record where state = 'open' and item_id is null and joined_to is null and id <> %s
          and type = any(%s) and cardinality(subjects) > 0 and last_seen >= now() - make_interval(hours => %s)
          and not exists (select 1 from plan_item p where p.exception_id = exception_record.id
                          and p.status in ('proposed', 'scheduled', 'in_progress'))
        order by first_seen
        """, (row["id"], joinable, hours)).fetchall():
        if mine & anchors(conn, o["subjects"]):
            conn.execute("update exception_record set joined_to = %s, item_id = %s, updated_at = now() where id = %s",
                         (row["id"], row["item_id"], o["id"]))
            work.comment(conn, row["item_id"], by, f"Also reported: {_label(conn, o['type'])} from {o['source']}: "
                                                   f"{o['title']}")
            conn.execute("update exception_record set value_usd = greatest(value_usd, %s), "
                         "priority = greatest(priority, %s) where id = %s", (o["value_usd"], o["priority"], row["id"]))
            n += 1
    return n


def _label(conn, type: str) -> str:
    t = types(conn).get(type)
    return t.label if t else type.replace("_", " ")


def _joined(conn, t: ExceptionType, row: dict, host: dict, by: str, unmatched: list[str]) -> dict:
    """A new exception that joined `host`'s case: noted on the case's history, the host carrying the larger value."""
    work.comment(conn, host["item_id"], by, f"Also reported: {_label(conn, row['type'])} from {row['source']}: "
                                            f"{row['title']}")
    conn.execute("update exception_record set value_usd = greatest(value_usd, %s), priority = greatest(priority, %s), "
                 "updated_at = now() where id = %s", (row["value_usd"], row["priority"], host["id"]))
    events.publish(conn, "exception.raised", f"exception:{row['id']}", {**_payload(row, t), "joined": host["id"]})
    return {**row, "raised": True, "joined": host["id"], "unmatched_note": _unmatched_note(conn, unmatched)}


def split(conn, exception_id: int, by: str, reason: str | None = None) -> dict:
    """Take an exception that joined another's case back out (the IOC says it is a different event): it gets a case of
    its own, routed as its type routes, and both cases' histories say so."""
    row = _row(conn, exception_id, lock=True)
    if row["state"] != OPEN or not row["joined_to"]:
        raise ExceptionError(f"exception {exception_id} isn't part of another's case")
    t = types(conn).get(row["type"]) or ExceptionType(row["type"], row["type"])
    old = row["item_id"]
    row = conn.execute("update exception_record set joined_to = null, item_id = null, updated_at = now() "
                       "where id = %s returning *", (exception_id,)).fetchone()
    item_id = _case(conn, t, row, by, None, None)
    if item_id:
        row = conn.execute("update exception_record set item_id = %s where id = %s returning *",
                           (item_id, exception_id)).fetchone()
    if old:
        work.comment(conn, old, by, f"Split out: {_label(conn, row['type'])}: {row['title']}"
                                    + (f" ({reason})" if reason else "") + (f", now case {item_id}" if item_id else ""))
    events.publish(conn, "exception.split", f"exception:{row['id']}", {**_payload(row, t), "by": by, "reason": reason,
                                                                       "from_case": old})
    return row


def _promote(conn, host: dict) -> None:
    """A host that cleared hands its case to the oldest exception still open on it, so the event stays one row."""
    heir = conn.execute("select id from exception_record where joined_to = %s and state = 'open' "
                        "order by first_seen limit 1", (host["id"],)).fetchone()
    if heir:
        conn.execute("update exception_record set joined_to = null where id = %s", (heir["id"],))
        conn.execute("update exception_record set joined_to = %s where (joined_to = %s or id = %s) and id <> %s",
                     (heir["id"], host["id"], host["id"], heir["id"]))


def on_case(conn, item_id: int) -> list[dict]:
    """Every exception a case works, the host first, each with what its source saw: the task page's sources."""
    rows = conn.execute("select * from exception_record where item_id = %s order by joined_to nulls first, first_seen",
                        (item_id,)).fetchall()
    return [{**{k: r[k] for k in ("id", "type", "key", "title", "source", "state", "value_usd", "first_seen",
                                  "last_seen", "joined_to", "subjects")},
             "label": _label(conn, r["type"]), "seen": _seen(r["evidence"])} for r in rows]


def raise_(conn, type: str, key: str, title: str, by: str, refs=(), priority: float | None = None,
           value_usd: float | None = None, evidence: dict | None = None, source: str | None = None,
           assignee: str | None = None, role: str | None = None) -> dict:
    t = _type(conn, type)
    if not key:
        raise ExceptionError("an exception needs a key: the source's own identity for the condition")
    subjects, unmatched = match(conn, refs)
    current = _close_if_case_finished(conn, conn.execute(
        "select * from exception_record where type = %s and key = %s and state = %s for update",
        (type, key, OPEN)).fetchone())
    if current:
        row = conn.execute(
            """
            update exception_record set title = %s, priority = coalesce(%s, priority),
                value_usd = coalesce(%s, value_usd), evidence = evidence || %s,
                subjects = array(select distinct unnest(subjects || %s::text[])),
                unmatched = array(select distinct unnest(unmatched || %s::text[])),
                raised_count = raised_count + 1, last_seen = now(), updated_at = now()
            where id = %s returning *
            """, (title, priority, value_usd, Jsonb(evidence or {}), subjects, unmatched, current["id"])).fetchone()
        if t.process and not row["joined_to"]:  # new evidence for the process already working it
            _case(conn, t, {**row, "evidence": evidence or {}}, by, assignee, role)
        return {**row, "raised": False, "unmatched_note": _unmatched_note(conn, unmatched)}
    host = _host(conn, t, type, key, subjects) if not (assignee or role) else None
    row = conn.execute(
        """
        insert into exception_record (type, key, title, source, priority, value_usd, subjects, unmatched, evidence,
                                      joined_to, item_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (type, key, title, source or by, priority, value_usd, subjects, unmatched, Jsonb(evidence or {}),
              host["id"] if host else None, host["item_id"] if host else None)).fetchone()
    if host:
        return _joined(conn, t, row, host, by, unmatched)
    # The work engine runs each operation as its own transaction: a case that can't open rolls this insert back
    # with it, and one that opens commits it.
    item_id = _case(conn, t, row, by, assignee, role)
    if item_id:
        row = conn.execute("update exception_record set item_id = %s where id = %s returning *",
                           (item_id, row["id"])).fetchone()
        if not (assignee or role):
            _adopt(conn, row, by)
    case = conn.execute("select assignee, role from work_item where id = %s", (item_id,)).fetchone() if item_id else None
    events.publish(conn, "exception.raised", f"exception:{row['id']}", {**_payload(row, t), **(case or {})})
    return {**row, "raised": True, "unmatched_note": _unmatched_note(conn, unmatched)}


def clear(conn, type: str, key: str, by: str, reason: str | None = None) -> dict | None:
    t = _type(conn, type)
    row = conn.execute("select * from exception_record where type = %s and key = %s and state = %s for update",
                       (type, key, OPEN)).fetchone()
    if row is None:
        return None
    row = conn.execute("update exception_record set state = 'cleared', closed_at = now(), updated_at = now() "
                       "where id = %s returning *", (row["id"],)).fetchone()
    # The case is the event's: it stays open while another source on it still holds (#464)
    shared = conn.execute("select 1 from exception_record where item_id = %s and state = 'open' and id <> %s limit 1",
                          (row["item_id"], row["id"])).fetchone() if row["item_id"] else None
    if shared and not row["joined_to"]:
        _promote(conn, row)
    if row["item_id"] and t.close_case_on_clear and not t.process and not shared:
        item = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()
        if item and item["state"] not in ("done", "cancelled"):
            work.close_case(conn, row["item_id"], "done", by, reason=reason or "the condition cleared")
    events.publish(conn, "exception.cleared", f"exception:{row['id']}", {**_payload(row, t), "reason": reason})
    return row


def reconcile(conn) -> dict:
    """Close open exceptions whose case finished (a person closed it, its process ended). If the condition
    still holds, its next raise opens a new exception and case."""
    rows = conn.execute(
        """
        update exception_record e set state = 'closed', closed_at = now(), updated_at = now()
        from work_item w where w.id = e.item_id and e.state = 'open' and w.state in ('done', 'cancelled')
        returning e.*
        """).fetchall()
    for row in rows:
        events.publish(conn, "exception.closed", f"exception:{row['id']}", _payload(row))
    return {"closed": len(rows)}


def listing(conn, state: str | None = OPEN, type: str | None = None, subject: str | None = None,
            audience: str | None = None, limit: int = 200) -> list[dict]:
    """Exceptions, most valuable then newest first, with the case that works them and who has it, and when it is
    planned if the IOC put it on the integrated plan instead of working it now. One event is one row (#464): the
    exceptions that joined its case are under it in `joined`, their assets in its `subjects`."""
    names = [n for n, t in types(conn).items() if audience is None or audience in t.audience]
    rows = conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role,
               e.raised_count + coalesce((select sum(j.raised_count) from exception_record j where j.joined_to = e.id), 0)::int as reports,
               (select json_build_object('id', p.id, 'start_at', p.start_at, 'end_at', p.end_at, 'crew', p.crew)
                from plan_item p where p.exception_id = e.id and p.status in ('proposed', 'scheduled', 'in_progress')
                order by p.start_at limit 1) as planned,
               coalesce((select json_agg(json_build_object('id', j.id, 'type', j.type, 'key', j.key, 'title', j.title,
                                                           'source', j.source, 'state', j.state, 'value_usd', j.value_usd,
                                                           'subjects', j.subjects, 'first_seen', j.first_seen)
                                         order by j.first_seen)
                         from exception_record j where j.joined_to = e.id), '[]') as joined
        from exception_record e
        left join work_item w on w.id = e.item_id
        where e.joined_to is null
          and (%(state)s::text is null or e.state = %(state)s)
          and (%(type)s::text is null or e.type = %(type)s
               or exists (select 1 from exception_record j where j.joined_to = e.id and j.type = %(type)s))
          and (%(subject)s::text is null or %(subject)s = any(e.subjects)
               or exists (select 1 from exception_record j where j.joined_to = e.id and %(subject)s = any(j.subjects)))
          and (%(all)s or e.type = any(%(types)s))
        order by e.value_usd desc nulls last, e.priority desc nulls last, e.last_seen desc limit %(limit)s
        """, {"state": state, "type": type, "subject": subject, "all": audience is None, "types": names,
              "limit": limit}).fetchall()
    for r in rows:
        if r["joined"]:
            labels = {n: t.label for n, t in types(conn).items()}
            for j in r["joined"]:
                j["label"] = labels.get(j["type"], j["type"].replace("_", " "))
            r["subjects"] = list(dict.fromkeys(r["subjects"] + [x for j in r["joined"] for x in j["subjects"]]))
    return rows


# ---- the IOC dispatching and the field reporting back -------------------------------------------------------------

FIELD_STATUSES = ("on_site", "done", "could_not_do", "note")
SEEN_SKIP = {"excursions", "priority", "health", "pre_diagnosis", "field", "exception_id", "type", "key", "upset_id",
             "incident_id", "first_unit", "first_detection"}
# What a field operator reads: the source's keys as words (the rest are spelled out from their key).
SEEN_LABELS = {"start": "started (data time)", "ongoing": "still going",
               "detections": "detections", "first_point": "first seen on", "notif_type": "notification type",
               "created": "created in SAP"}


def _row(conn, exception_id: int, lock: bool = False) -> dict:
    row = conn.execute("select * from exception_record where id = %s" + (" for update" if lock else ""),
                       (exception_id,)).fetchone()
    if row is None:
        raise ExceptionError(f"no exception {exception_id}")
    return row


def _due(conn, row: dict):
    """When a dispatched SAP notification is due, from its SAP priority (`intake.sap_notifications.due_hours`), on
    today's clock from when Fuzer raised it (#494). None for other types, an unmapped priority or the setting off."""
    from datetime import timedelta

    from ..core import settings

    if row["type"] != "sap_notification" or not (p := str((row["evidence"] or {}).get("priority") or "").strip()[:1]):
        return None
    hours = dict(x.split(":", 1) for x in str(settings.get(conn, "intake.sap_notifications.due_hours") or "").split(",")
                 if ":" in x)
    try:
        return row["first_seen"] + timedelta(hours=float(hours[p])) if p in hours else None
    except ValueError:
        return None


def dispatch(conn, exception_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Send an exception to someone (a person, `role:<role>`): open its case for them if it has none, else hand
    its case (or the case's open person step) to them. Publishes exception.dispatched."""
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:  # part of another's case: the event is dispatched once, through its host
        return dispatch(conn, row["joined_to"], by, to, reason)
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {exception_id} is {row['state']}")
    t = types(conn).get(row["type"]) or ExceptionType(row["type"], row["type"])
    if row["item_id"]:
        work.dispatch(conn, row["item_id"], by, to, reason)
    else:
        item_id = work.open_case(conn, t.case_type or t.name, row["title"], by, subjects=row["subjects"],
                                 priority=row["priority"], due=_due(conn, row), assignee=None if to.startswith("role:") else to,
                                 role=to.removeprefix("role:") if to.startswith("role:") else None, source=row["source"],
                                 payload={**row["evidence"], "exception_id": row["id"]}, reason=reason)["id"]
        conn.execute("update exception_record set item_id = %s, updated_at = now() where id = %s", (item_id, row["id"]))
        _adopt(conn, _row(conn, exception_id), by)
    row = _row(conn, exception_id)
    events.publish(conn, "exception.dispatched", f"exception:{row['id']}", {**_payload(row, t), "to": to, "by": by,
                                                                            "reason": reason})
    return row


def report(conn, exception_id: int, by: str, status: str, note: str | None = None) -> dict:
    """What a field operator says back: on site, done, could not do (with why) or a note. Kept on the exception's
    evidence (`field`) and its case's history, and published as exception.reported for the IOC. `done` on a case
    without a process closes it. `done` on a process case, or `could_not_do` on any, hands what was sent to them
    back to whoever sent it (the IOC's step goes back to its queue), so the IOC decides what happens next."""
    if status not in FIELD_STATUSES:
        raise ExceptionError(f"status must be one of {FIELD_STATUSES}")
    if status in ("could_not_do", "note") and not (note or "").strip():
        raise ExceptionError("say why (or what) in the note")
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:  # the field reports on the event, which its host carries
        return report(conn, row["joined_to"], by, status, note)
    entry = {"at": conn.execute("select now() as t").fetchone()["t"].isoformat(), "by": by, "status": status,
             "note": note}
    conn.execute("update exception_record set evidence = jsonb_set(evidence, '{field}', "
                 "coalesce(evidence->'field', '[]') || %s), updated_at = now() where id = %s",
                 (Jsonb([entry]), exception_id))
    if row["item_id"]:
        said = {"on_site": "On site", "done": "Done", "could_not_do": "Could not do", "note": "Note"}[status]
        work.comment(conn, row["item_id"], by, f"Field: {said}" + (f": {note}" if note else ""))
        has_process = conn.execute("select 1 from work_process where item_id = %s", (row["item_id"],)).fetchone()
        state = conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()["state"]
        if status == "done" and not has_process and state not in ("done", "cancelled"):
            work.close_case(conn, row["item_id"], "done", by, reason=note or "done in the field")
            if _close_if_case_finished(conn, row) is None:  # closed with its case now, not at the next work tick
                events.publish(conn, "exception.closed", f"exception:{row['id']}", _payload(_row(conn, exception_id)))
        elif status in ("done", "could_not_do") and state not in ("done", "cancelled"):
            # The field's part is over. A process with a field step of its own (the upset's Field check) has it
            # completed with what they found, and the IOC's decision it returns to goes to whoever sent them; else
            # what the IOC sent them (its decision step, or the case) goes back to whoever sent it. Either way it
            # lands in that person's or queue's Inbox, with the reason saying it is back from the field.
            back = f"Back from the field: {said.lower()}" + (f": {note.strip()[:140]}" if note else "")
            if not (has_process and work.report_field_step(conn, row["item_id"], by, status, note, back)):
                work.hand_back(conn, row["item_id"], by, by, reason=back)
    row = _row(conn, exception_id)
    from . import closeout

    closeout.on_field_report(conn, row, by, status, note)  # the system that raised it hears what the field did (#466)
    events.publish(conn, "exception.reported", f"exception:{row['id']}", {**_payload(row), **entry})
    return row


# SAP's own status and notification type codes, as words (#494); the code stays beside them.
SAP_WORDS = {"status": {"OSNO": "outstanding", "NOPR": "in process", "NOCO": "completed", "ORAS": "order assigned"},
             "notif_type": {"M1": "maintenance request", "M2": "malfunction report", "M3": "activity report"}}


@functools.cache
def _iso14224() -> dict:
    from ..core.taxonomy import load_standard

    std = load_standard("iso14224")
    return {"failure_mode": std.get("failure_modes", {}), "failure_mechanism": std.get("failure_mechanisms", {})}


def _worded(k: str, text: str) -> str:
    """A code a field operator can't read as the words it stands for, with the code after it: `VIB` reads
    "Vibration (VIB)", SAP status `OSNO` reads "outstanding (OSNO)". Unknown codes stay as they are."""
    words = SAP_WORDS.get(k, {}).get(text.upper()) or _iso14224().get(k, {}).get(text)
    return f"{words} ({text})" if words else text


def _seen(evidence: dict) -> list[tuple[str, str]]:
    """The evidence a person reads at a glance: short scalar values, not the nested detail. A site's damage and
    cause codes are left out where the ISO 14224 failure mode and mechanism they map to are there to read."""
    out = []
    for k, v in evidence.items():
        if k in SEEN_SKIP or isinstance(v, (dict, list)) or v is None or v == "":
            continue
        if (k == "damage_code" and evidence.get("failure_mode")) or (k == "cause_code" and evidence.get("failure_mechanism")):
            continue
        if isinstance(v, bool):
            text = "yes" if v else "no"
        elif isinstance(v, float):
            text = f"{v:g}"
        else:
            text = str(v)[:120]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", text):
                text = text.replace("T", " ")[:16]
            text = _worded(k, text)
        label = "assets affected" if k == "units" and isinstance(v, int) else SEEN_LABELS.get(k, k.replace("_", " "))
        out.append((label, text))  # an upset's `units` counts assets; another source's names a unit of measure
    return out[:12]


def _sent(conn, item_id: int, person: str) -> dict | None:
    """Who sent this job to `person`, when, and what they asked for: the latest hand-off to them, else the case
    being opened for them or for the queue they took it from (taking it isn't being sent it)."""
    return conn.execute(
        """
        select actor as by, at, data->>'reason' as reason from work_event
        where item_id = %(i)s and ((kind = 'handed_off' and data->>'to' = %(p)s and actor <> %(p)s)
                                   or (kind = 'created' and (data->>'assignee' = %(p)s
                                       or (actor <> %(p)s and actor not like 'agent:%%'))))
        order by id desc limit 1
        """, {"i": item_id, "p": person}).fetchone()  # taking a job from the queue isn't being sent it


def _asked(conn, item_id: int) -> dict | None:
    """Who sent a process to the field and when: whoever last completed a step with a field ask (an engineer's
    Triage choosing a field check), not the operator who then took it from the queue."""
    return conn.execute(
        "select actor as by, at from work_event where item_id = %s and kind = 'completed' "
        "and coalesce(data->'data'->>'field_ask', '') <> '' order by id desc limit 1", (item_id,)).fetchone()


def _assets(conn, ids: list[str]) -> list[dict]:
    """Each subject with its label, kind and a location: its own, else the nearest located thing it is part of.
    Equipment reads as the tag it is installed at and its description ("20-K-2100B 1st stage compressor"), not its
    serial number, since that is what an operator finds on site."""
    if not ids:
        return []
    rows = conn.execute(
        """
        with recursive up (start, fuzer_id, depth) as (
            select s, s, 0 from unnest(%s::text[]) s
            union all
            select up.start, e.dst, up.depth + 1 from up join edge e on e.src = up.fuzer_id and e.rel = 'part_of'
            where up.depth < 3)
        select distinct on (s.id) s.id as fuzer_id, coalesce(t.label || coalesce(' ' || (d.value #>> '{}'), ''),
               l.label) as label, en.kind, g.cy as lat, g.cx as lon
        from unnest(%s::text[]) with ordinality s (id, n)
        left join entity_label l on l.fuzer_id = s.id
        left join entity en on en.fuzer_id = s.id
        left join golden d on d.fuzer_id = s.id and d.field = 'description'
        left join lateral (select tl.label from edge x join entity_label tl on tl.fuzer_id = x.dst
                           where x.src = s.id and x.rel = 'installed_at' and x.valid_to is null
                           order by tl.label limit 1) t on en.kind = 'equipment'
        left join up on up.start = s.id
        left join geo_index g on g.fuzer_id = up.fuzer_id
        order by s.id, g.fuzer_id is null, up.depth
        """, (ids, ids)).fetchall()
    order = {f: i for i, f in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r["fuzer_id"]])


def field_jobs(conn, person: str) -> list[dict]:
    """The open exceptions whose case is with this person (the case, or its open person step), most urgent first,
    then the ones waiting unclaimed in their roles' queues (`queued`, for them to take), each with where to go, what
    was seen, what happened at the asset lately and the field log."""
    rows = conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role, coalesce(t.due_at, w.due) as due,
               e.raised_count + coalesce((select sum(j.raised_count) from exception_record j where j.joined_to = e.id), 0)::int as reports,
               t.id as task_id, t.name as task_name, t.role as task_role, p.data->>'field_ask' as ask,
               not (coalesce(t.assignee = %(p)s, false) or (t.id is null and coalesce(w.assignee = %(p)s, false)))
                 as queued
        from exception_record e join work_item w on w.id = e.item_id
        left join lateral (select id, name, due_at, assignee, role from work_token
                           where item_id = w.id and kind = 'user' and state in ('ready', 'waiting')
                           order by due_at nulls last, id limit 1) t on true
        left join work_process p on p.id = (select process_id from work_token where id = t.id)
        where e.state = 'open' and e.joined_to is null and w.state not in ('done', 'cancelled')
          and (t.assignee = %(p)s or (t.id is null and w.assignee = %(p)s)
               or (t.id is not null and t.assignee is null and t.role = any(%(roles)s))
               or (t.id is null and w.assignee is null and w.role = any(%(roles)s)))
        order by coalesce(t.assignee = %(p)s, false) or (t.id is null and coalesce(w.assignee = %(p)s, false)) desc,
                 coalesce(t.due_at, w.due) nulls last, e.value_usd desc nulls last, e.priority desc nulls last
        """, {"p": person, "roles": (roles := work.roles_of(conn, person))}).fetchall()
    out, known = [], types(conn)
    for r in rows:
        # Their own step (a Field check, in the operator role) can be opened; another role's step handed to them
        # (an engineer's Triage) can't: they report back from here and it returns to whoever sent it.
        r["own_step"] = bool(r["task_id"]) and r["task_role"] in roles
        sent = _sent(conn, r["item_id"], person)
        if r["ask"] and r["task_role"] == "operator":  # what the IOC asked when it sent the process to the field
            sent = _asked(conn, r["item_id"]) or {"by": None, "at": None}
            sent = {**sent, "reason": r["ask"]}
        history = conn.execute(
            "select id, title, state, first_seen from exception_record where id <> %s and subjects && %s "
            "and item_id is distinct from %s and first_seen > now() - interval '90 days' "
            "order by first_seen desc limit 5",
            (r["id"], r["subjects"], r["item_id"])).fetchall() if r["subjects"] else []
        t = known.get(r["type"])
        sources = on_case(conn, r["item_id"])
        subjects = list(dict.fromkeys(x for src in sources for x in src["subjects"])) or r["subjects"]
        out.append({**r, "label": t.label if t else r["type"].replace("_", " "), "assets": _assets(conn, subjects),
                    "sources": sources if len(sources) > 1 else [],
                    "seen": _seen(r["evidence"]), "history": history, "field": r["evidence"].get("field", []),
                    "sent": sent})
    return out



# ---- exception types as data: other systems' exceptions without code ------------------------------------------

def define(conn, name: str, by: str, label: str, role: str | None = None, process: str | None = None,
           case_type: str | None = None, audience=("ioc",), close_case_on_clear: bool = True,
           description: str | None = None, joins: bool = True, source_system: str | None = None) -> dict:
    """Define (or change) an exception type as data, so another system (SAP notifications, SCADA alarms, a
    reporting app) can raise it over the API. Types modules declare in code can't be redefined here.
    `source_system` is the system whose source pack brought it (#465): that system's webhook may raise it."""
    from ..core.exceptions import AUDIENCES

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name or ""):
        raise ExceptionError("a type name is lower case letters, digits and _ (sap_notification, say)")
    if name in TYPES:
        raise ExceptionError(f"{name} is declared in code ({TYPES[name].label}); it can't be redefined as data")
    if bad := set(audience) - set(AUDIENCES):
        raise ExceptionError(f"audience must be among {AUDIENCES}, not {sorted(bad)}")
    if not (label or "").strip():
        raise ExceptionError("a type needs a label")
    if role:  # a queue nobody can be in would hold its cases where no Inbox shows them
        queue = role.removeprefix("role:")
        known = work.person_roles(conn)
        if queue not in known:
            raise ExceptionError(f"no role queue '{queue}'; pick one of {', '.join(known)} (or add the role on Teams)")
        role = f"role:{queue}"
    return conn.execute(
        """
        insert into exception_kind (name, label, role, process, case_type, audience, close_case_on_clear, description,
                                    updated_by, joins, source_system)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (name) do update set label = excluded.label, role = excluded.role, process = excluded.process,
            case_type = excluded.case_type, audience = excluded.audience,
            close_case_on_clear = excluded.close_case_on_clear, description = excluded.description, joins = excluded.joins,
            source_system = coalesce(excluded.source_system, exception_kind.source_system),
            updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (name, label.strip(), role or None, process or None, case_type or None, list(audience), close_case_on_clear,
              description, by, joins, source_system or None)).fetchone()


def undefine(conn, name: str) -> bool:
    """Remove a type defined as data; refused while any exception of it is open."""
    if conn.execute("select 1 from exception_record where type = %s and state = 'open' limit 1", (name,)).fetchone():
        raise ExceptionError(f"{name} has open exceptions; clear them first")
    return conn.execute("delete from exception_kind where name = %s", (name,)).rowcount > 0


def _open_counts(conn) -> dict[str, int]:
    return {r["type"]: r["n"] for r in conn.execute(
        "select type, count(*) as n from exception_record where state = 'open' group by type")}


def type_listing(conn) -> list[dict]:
    """Every type, where it is defined (code or data), the system that brought it, and how many of it are open."""
    open_n = _open_counts(conn)
    brought = {r["name"]: r["source_system"] for r in conn.execute("select name, source_system from exception_kind")}
    return [{"name": n, "label": t.label, "role": t.role, "process": t.process, "case_type": t.case_type,
             "audience": list(t.audience), "close_case_on_clear": t.close_case_on_clear, "description": t.description,
             "joins": t.joins, "source_system": None if n in TYPES else brought.get(n),
             "defined_in": "code" if n in TYPES else "data", "open": open_n.get(n, 0)}
            for n, t in sorted(types(conn).items())]


def defined_types(conn, source_system: str | None = None) -> list[dict]:
    """Types defined as data (all, or the ones a system's pack brought), with how many of each are open."""
    open_n = _open_counts(conn)
    return [{**dict(r), "audience": list(r["audience"]), "open": open_n.get(r["name"], 0)} for r in conn.execute(
        "select * from exception_kind where %(s)s::text is null or source_system = %(s)s order by name",
        {"s": source_system})]


def open_keys(conn, type: str, rule: str | None = None) -> set[str]:
    """Keys of a type's open exceptions; with `rule`, only the ones that source rule raised (evidence.rule)."""
    return {r["key"] for r in conn.execute(
        "select key from exception_record where type = %(t)s and state = 'open' "
        "and (%(r)s::text is null or evidence->>'rule' = %(r)s)", {"t": type, "r": rule})}


@events.subscriber("exception.dispatched")
def tell_who_was_sent(conn, event: dict) -> None:
    """Post a dispatch to the alert webhook (Teams / Slack), naming who was sent, so a field operator hears of it
    without watching the app."""
    from ..sources import alerts

    e = event["payload"]
    who = e.get("to") or ""
    alerts.post(f"Dispatched to {who.removeprefix('role:')}: {e.get('title')}"
                + (f" ({e['reason']})" if e.get("reason") else ""))

"""The built-in exception implementation behind fuzer.core.exceptions: one `exception_record` per open condition,
matched to entities, routed to a case through the Work port, and published on the event outbox.

    raise_(conn, type, key, title, by, refs=..., priority=..., value_usd=..., finding=..., source=..., assignee=...)
    clear(conn, type, key, by, reason)
    split(conn, exception_id, by)   take one that joined another's case back out to a case of its own
    join(conn, exception_id, into, by)  the reverse: bring one with its own case into another's, cancelling its own
    reconcile(conn)          close the ones whose case finished, unshelve expired ones (the work tick runs it)
    shelve(conn, exception_id, by, reason, until) / unshelve(...)  set a standing condition aside, with an expiry
    match(conn, refs)        -> (fuzer ids, refs that matched nothing)
    listing(conn, ...)       what the IOC (or the field) sees

States: open (the condition holds), cleared (it went away) and closed (its case finished while it held; the next
raise opens a new exception and a new case, so a closed case never hides a condition that came back).

One event, one case (#464): a new exception about the same asset as an open one from another source (the same
entity, or one sharing the equipment or unit it is part of), raised within `exceptions.join_hours` of it, joins that
one's case instead of opening another. The first is the host (`joined_to` null); the others point at it and share its
case, so the IOC triages, dispatches and closes once. Exceptions lists the host with every source under it.
"""

import re

from psycopg.types.json import Jsonb

from ..core import events, findings, gates, work
from ..core.exceptions import TYPES, ExceptionError, ExceptionType

OPEN = "open"


KIND_FIELDS = ("label", "role", "process", "case_type", "audience", "close_case_on_clear", "description", "joins")


def types(conn) -> dict[str, ExceptionType]:
    """Every exception type: the ones a deployment defined as data (exception_kind, for other systems' exceptions)
    and the ones modules declare in code, which win on a clash."""
    from ..core import exceptions as port

    port.load()
    data = {r["name"]: ExceptionType(r["name"], r["label"], role=r["role"], process=r["process"],
                                     case_type=r["case_type"], audience=tuple(r["audience"]),
                                     close_case_on_clear=r["close_case_on_clear"], description=r["description"] or "",
                                     joins=r.get("joins", True), due_hours=r.get("due_hours"))
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
    """Open the case that works it, through the one front door for work (routing.route, #509)."""
    from . import routing

    return routing.route(conn, row, by, assignee=assignee, role=role)


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
    return set().union(*anchor_map(conn, ids).values()) if ids else set()


def anchor_map(conn, ids: list[str]) -> dict[str, set[str]]:
    """`anchors` for each of `ids` on its own, in one query: what a point or a log entry's mention stands for, to
    compare with an event's (`around`)."""
    if not ids:
        return {}
    out: dict[str, set[str]] = {i: set() for i in ids}
    for r in conn.execute(
        """
        with recursive up (seed, id, depth) as (
            select s, s, 0 from unnest(%s::text[]) s
            union
            select up.seed, case when e.rel = 'has_timeseries' then e.src else e.dst end, up.depth + 1
            from up join edge e on (e.rel = 'has_timeseries' and e.dst = up.id)
                                or (e.rel in ('part_of', 'installed_at') and e.src = up.id and e.valid_to is null)
            where up.depth < 3)
        select distinct up.seed, up.id from up join entity x on x.fuzer_id = up.id
        where x.kind not in ('area', 'facility', 'site', 'field')
        """, (list(ids),)):
        out[r["seed"]].add(r["id"])
    return out


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


def join(conn, exception_id: int, into: int, by: str, reason: str | None = None) -> dict:
    """The reverse of `split`: the IOC says an exception with a case of its own is part of the same event as `into`
    (a cascade the automatic join missed). It and everything on its case join `into`'s case, the case it had is
    cancelled, and both histories say so: one case to triage, one dispatch, one close-out."""
    row = _row(conn, exception_id, lock=True)
    host = _row(conn, into, lock=True)
    if host["joined_to"]:
        host = _row(conn, host["joined_to"], lock=True)
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {exception_id} is {row['state']}, not open")
    if host["state"] != OPEN or not host["item_id"]:
        raise ExceptionError(f"exception {host['id']} has no open case to join")
    item = conn.execute("select state from work_item where id = %s", (host["item_id"],)).fetchone()
    if not item or item["state"] in ("done", "cancelled"):
        raise ExceptionError(f"case {host['item_id']} is closed")
    old = row["item_id"]
    if old == host["item_id"] or row["id"] == host["id"]:
        raise ExceptionError(f"exception {exception_id} is already on case {host['item_id']}")
    moved = conn.execute(
        "update exception_record set joined_to = %s, item_id = %s, updated_at = now() "
        "where id <> %s and (id = %s or joined_to = %s or (%s::bigint is not null and item_id = %s)) returning id",
        (host["id"], host["item_id"], host["id"], row["id"], row["id"], old, old)).fetchall()
    conn.execute("update exception_record set value_usd = greatest(value_usd, %s), priority = greatest(priority, %s), "
                 "updated_at = now() where id = %s", (row["value_usd"], row["priority"], host["id"]))
    row = _row(conn, exception_id)
    t = types(conn).get(row["type"])
    note = (f"Merged in: {_label(conn, row['type'])} from {row['source']}: {row['title']}"
            + (f" ({reason})" if reason else "") + (f", was case {old}" if old else ""))
    work.comment(conn, host["item_id"], by, note)
    if old:
        work.cancel_case(conn, old, by, reason=f"Merged into case {host['item_id']}" + (f": {reason}" if reason else ""))
    events.publish(conn, "exception.joined", f"exception:{row['id']}", {**_payload(row, t), "by": by, "reason": reason,
                                                                        "from_case": old, "moved": len(moved)})
    return row


def _promote(conn, host: dict) -> None:
    """A host that cleared hands its case to the oldest exception still open on it, so the event stays one row."""
    heir = conn.execute("select id from exception_record where joined_to = %s and state = 'open' "
                        "order by first_seen limit 1", (host["id"],)).fetchone()
    if heir:
        conn.execute("update exception_record set joined_to = null where id = %s", (heir["id"],))
        conn.execute("update exception_record set joined_to = %s where (joined_to = %s or id = %s) and id <> %s",
                     (heir["id"], host["id"], host["id"], heir["id"]))


def _findings(conn, rows: list[dict]) -> list[dict]:
    """Each exception's finding as every reader gets it (core.findings.shown), its assets named."""
    ids = list(dict.fromkeys(x for r in rows for x in findings.asset_ids(r["evidence"])))
    names = {a["fuzer_id"]: a["label"] for a in _assets(conn, ids) if a["label"]}
    return [findings.shown(r["evidence"], r["source"], r["subjects"], r["value_usd"], names) for r in rows]


def on_case(conn, item_id: int) -> list[dict]:
    """Every exception a case works, the host first, each with its finding: the task page's sources."""
    rows = conn.execute("select * from exception_record where item_id = %s order by joined_to nulls first, first_seen",
                        (item_id,)).fetchall()
    return [{**{k: r[k] for k in ("id", "type", "key", "title", "source", "state", "value_usd", "first_seen",
                                  "last_seen", "joined_to", "subjects")},
             "label": _label(conn, r["type"]), "finding": f}
            for r, f in zip(rows, _findings(conn, rows), strict=True)]


def raise_(conn, type: str, key: str, title: str, by: str, refs=(), priority: float | None = None,
           value_usd: float | None = None, finding: findings.Finding | None = None, source: str | None = None,
           assignee: str | None = None, role: str | None = None, first_role: str | None = None,
           mute: str | None = None) -> dict:
    """Open an exception of `type` for `key`, or update the open one, from a finding (the contract in
    core.findings; with none, a bare one from the source that says only who raised it). `first_role` sends the case
    it opens there instead of its type's role, and still joins an open case; `mute` (the reason) counts and keeps a
    new condition with no case (a provider set so, P3.8b)."""
    t = _type(conn, type)
    evidence = (finding or findings.external(source or by)).evidence()
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
            """, (title, priority, value_usd, Jsonb(evidence), subjects, unmatched, current["id"])).fetchone()
        if t.process and not row["joined_to"]:  # new evidence for the process already working it
            _case(conn, t, {**row, "evidence": evidence}, by, assignee, role)
        return {**row, "raised": False, "unmatched_note": _unmatched_note(conn, unmatched)}
    if not (assignee or role):  # planned work or a shutdown on the asset explains it: counted, no case (P3.5b)
        from . import suppression

        if suppression.applies(conn, t) and (w := suppression.covering(conn, subjects, suppression._at(evidence))):
            return suppression.record(conn, w, type, key, title, source or by, subjects, evidence, value_usd)
        if mute:
            w = {"kind": "muted", "id": 0, "reason": mute, "until": None}
            return suppression.record(conn, w, type, key, title, source or by, subjects, evidence, value_usd)
    host = _host(conn, t, type, key, subjects) if not (assignee or role) else None
    row = conn.execute(
        """
        insert into exception_record (type, key, title, source, priority, value_usd, subjects, unmatched, evidence,
                                      joined_to, item_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (type, key, title, source or by, priority, value_usd, subjects, unmatched, Jsonb(evidence),
              host["id"] if host else None, host["item_id"] if host else None)).fetchone()
    if host:
        return _joined(conn, t, row, host, by, unmatched)
    # The work engine runs each operation as its own transaction: a case that can't open rolls this insert back
    # with it, and one that opens commits it.
    item_id = _case(conn, t, row, by, assignee, role or (first_role if not assignee else None))
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
        from ..core import gates

        item = conn.execute("select state, type from work_item where id = %s", (row["item_id"],)).fetchone()
        if item and item["state"] not in ("done", "cancelled"):
            if item["type"] in gates.safety_case_types(conn) and gates.machine(by):
                # a safety or environmental case is closed by a person (P5.10 fixed gate): it stays open, saying so
                work.comment(conn, row["item_id"], by, f"The condition cleared ({reason or 'cleared'}). A safety or "
                             "environmental case is closed by a person: check it and close it.")
            else:
                work.close_case(conn, row["item_id"], "done", by, reason=reason or "the condition cleared")
    events.publish(conn, "exception.cleared", f"exception:{row['id']}", {**_payload(row, t), "reason": reason})
    return row


def reconcile(conn) -> dict:
    """Close open exceptions whose case finished (a person closed it, its process ended). If the condition
    still holds, its next raise opens a new exception and case. Shelved ones whose expiry passed go back on the list."""
    rows = conn.execute(
        """
        update exception_record e set state = 'closed', closed_at = now(), updated_at = now()
        from work_item w where w.id = e.item_id and e.state = 'open' and w.state in ('done', 'cancelled')
        returning e.*
        """).fetchall()
    for row in rows:
        events.publish(conn, "exception.closed", f"exception:{row['id']}", _payload(row))
    return {"closed": len(rows), "unshelved": len(unshelve_expired(conn))}


def shelve(conn, exception_id: int, by: str, reason: str, until) -> dict:
    """Set a standing condition aside (P3.5c, #694): off the open list, with a reason, until `until`. New raises still
    add their evidence; when the expiry passes the work tick puts it back. Never a safety case type (`gates`): an
    integrity window breach stays in front of the IOC."""
    from datetime import UTC, datetime, timedelta

    from ..core import gates, settings

    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:
        row = _row(conn, row["joined_to"], lock=True)  # the event is one row: its host is what leaves the list
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {row['id']} is {row['state']}, not open")
    if row["type"] in gates.safety_case_types(conn):
        raise ExceptionError(f"{_label(conn, row['type'])} is a safety case type: it can't be shelved")
    if not (reason or "").strip():
        raise ExceptionError("shelving needs a reason")
    at = until if isinstance(until, datetime) else datetime.fromisoformat(str(until))
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    now = conn.execute("select now() as t").fetchone()["t"]
    days = settings.get(conn, "exceptions.max_shelve_days")
    if at <= now:
        raise ExceptionError("shelving needs an expiry in the future")
    if at > now + timedelta(days=days):
        raise ExceptionError(f"shelving is for at most {days} days (exceptions.max_shelve_days)")
    row = conn.execute("update exception_record set shelved_until = %s, shelved_by = %s, shelved_reason = %s, "
                       "updated_at = now() where id = %s returning *", (at, by, reason.strip(), row["id"])).fetchone()
    if row["item_id"]:
        work.comment(conn, row["item_id"], by, f"Shelved until {at:%Y-%m-%d %H:%M} UTC: {reason.strip()}")
    events.publish(conn, "exception.shelved", f"exception:{row['id']}",
                   {**_payload(row), "by": by, "reason": row["shelved_reason"], "until": at.isoformat()})
    return row


def unshelve(conn, exception_id: int, by: str, why: str = "taken off the shelf") -> dict:
    """Put a shelved exception back on the open list now."""
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:
        row = _row(conn, row["joined_to"], lock=True)
    if row["shelved_until"] is None:
        raise ExceptionError(f"exception {row['id']} isn't shelved")
    return _unshelved(conn, row, by, why)


def _unshelved(conn, row: dict, by: str, why: str) -> dict:
    row = conn.execute("update exception_record set shelved_until = null, updated_at = now() where id = %s "
                       "returning *", (row["id"],)).fetchone()
    if row["item_id"]:
        work.comment(conn, row["item_id"], by, f"Back on the list: {why} (it was shelved: {row['shelved_reason']})")
    events.publish(conn, "exception.unshelved", f"exception:{row['id']}", {**_payload(row), "by": by, "why": why})
    return row


def unshelve_expired(conn) -> list[dict]:
    """Every shelved exception whose expiry passed, back on the open list (the work tick, through `reconcile`)."""
    return [_unshelved(conn, r, "system", "its shelving expired") for r in conn.execute(
        "select * from exception_record where shelved_until is not null and shelved_until <= now() for update").fetchall()]


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
          and (%(state)s::text is null or e.state = %(state)s
               or (%(state)s = 'shelved' and e.state = 'open' and e.shelved_until > now()))
          and (%(state)s::text is distinct from 'open' or e.shelved_until is null or e.shelved_until <= now())
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

def _row(conn, exception_id: int, lock: bool = False) -> dict:
    row = conn.execute("select * from exception_record where id = %s" + (" for update" if lock else ""),
                       (exception_id,)).fetchone()
    if row is None:
        raise ExceptionError(f"no exception {exception_id}")
    return row


def _due(conn, row: dict):
    """When a dispatched exception is due, by its type's due policy (routing.due: SAP priority hours, #494)."""
    from . import routing

    return routing.due(conn, row)


def dispatch(conn, exception_id: int, by: str, to: str, reason: str | None = None, *, override: str | None = None,
             blockers: list[str] | None = None) -> dict:
    """Send an exception to someone (a person, `role:<role>`): open its case for them if it has none, else hand
    its case (or the case's open person step) to them. Publishes exception.dispatched. Dispatching is a fixed gate
    (P5.10): only a named person dispatches."""
    from ..core import gates

    gates.require_person(conn, "field_dispatch", by, ref=f"exception:{exception_id}")
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:  # part of another's case: the event is dispatched once, through its host
        return dispatch(conn, row["joined_to"], by, to, reason, override=override, blockers=blockers)
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {exception_id} is {row['state']}")
    t = types(conn).get(row["type"]) or ExceptionType(row["type"], row["type"])
    if row["item_id"]:
        work.dispatch(conn, row["item_id"], by, to, reason)
    else:
        from . import routing

        item_id = routing.route(conn, row, by, to=to, reason=reason)
        conn.execute("update exception_record set item_id = %s, updated_at = now() where id = %s", (item_id, row["id"]))
        _adopt(conn, _row(conn, exception_id), by)
    row = _row(conn, exception_id)
    if override:  # sent though not ready (P5.5): what blocked it and why it went anyway, on the case's history
        work.comment(conn, row["item_id"], by, f"Sent though not ready ({'; '.join(blockers or [])}). Why: {override}")
    events.publish(conn, "exception.dispatched", f"exception:{row['id']}", {**_payload(row, t), "to": to, "by": by,
                                                                            "reason": reason, "override": override})
    return row


PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
PHOTO_MAX = 2_000_000  # bytes, after the phone resized it
PHOTOS_MAX = 4


def _reading(reading: dict | None) -> dict | None:
    """A reading the field took: a number with its unit, and what it was of."""
    if not reading:
        return None
    try:
        value = float(reading.get("value"))
    except (TypeError, ValueError):
        raise ExceptionError("a reading needs a number") from None
    unit = str(reading.get("unit") or "").strip()
    if not unit or len(unit) > 20:
        raise ExceptionError("a reading needs its unit (°C, barg, mm/s...)")
    what = str(reading.get("what") or "").strip()[:80] or None
    return {"value": value, "unit": unit, "what": what}


def reading_text(r: dict) -> str:
    v = f"{r['value']:g}"
    return f"{r['what']} {v} {r['unit']}" if r.get("what") else f"{v} {r['unit']}"


def _photos(photos) -> list[tuple[str, bytes]]:
    """Photos as data URLs (data:image/jpeg;base64,...), checked for type and size."""
    import base64

    out = []
    for p in list(photos or [])[:PHOTOS_MAX + 1]:
        head, _, body = str(p).partition(",")
        mime = head.removeprefix("data:").removesuffix(";base64")
        if not head.startswith("data:") or mime not in PHOTO_TYPES:
            raise ExceptionError("a photo must be a JPEG, PNG or WebP image")
        try:
            data = base64.b64decode(body, validate=True)
        except ValueError:
            raise ExceptionError("a photo could not be read") from None
        if len(data) > PHOTO_MAX:
            raise ExceptionError(f"a photo is too large ({len(data) // 1000} kB, at most {PHOTO_MAX // 1000} kB)")
        out.append((mime, data))
    if len(out) > PHOTOS_MAX:
        raise ExceptionError(f"at most {PHOTOS_MAX} photos on one report")
    return out


def photo(conn, exception_id: int, photo_id: int) -> dict:
    row = conn.execute("select mime, data from field_photo where id = %s and exception_id = %s",
                       (photo_id, exception_id)).fetchone()
    if row is None:
        raise ExceptionError(f"no photo {photo_id} on exception {exception_id}")
    return row


def _when(conn, at) -> tuple[str, bool]:
    """When a report was made: the phone's time for one sent later (P5.3c), never in the future or more than a week
    back; else now. Also whether it was made well before it arrived."""
    from datetime import datetime, timedelta

    now = conn.execute("select now() as t").fetchone()["t"]
    if not at:
        return now.isoformat(), False
    try:
        made = datetime.fromisoformat(str(at))
    except ValueError:
        raise ExceptionError("the report's time is not a date and time") from None
    if made.tzinfo is None:
        made = made.replace(tzinfo=now.tzinfo)
    made = min(max(made, now - timedelta(days=7)), now)
    return made.isoformat(), now - made > timedelta(minutes=2)


def report(conn, exception_id: int, by: str, status: str, note: str | None = None, *, reading: dict | None = None,
           photos=(), client_id: str | None = None, at: str | None = None, override: str | None = None) -> dict:
    """What a field operator says back: on site, done, could not do (with why) or a note. Kept on the exception's
    evidence (`field`) and its case's history, and published as exception.reported for the IOC. `done` on a case
    without a process closes it. `done` on a process case, or `could_not_do` on any, hands what was sent to them
    back to whoever sent it (the IOC's step goes back to its queue), so the IOC decides what happens next.
    A report can carry a reading (a number with its unit) and photos (P5.3b), which the IOC sees with it.
    A report the phone kept while it had no signal (P5.3c) comes with its `client_id` and the time it was made: it is
    applied once (a resend changes nothing) and keeps that time.
    "On site" starts the work: on a job whose work type needs a permit to work and has no valid one (P5.11a), it is
    refused unless the person says why it starts anyway (`override`), a fixed human gate kept on the case."""
    if status not in FIELD_STATUSES:
        raise ExceptionError(f"status must be one of {FIELD_STATUSES}")
    if status in ("could_not_do", "note") and not (note or "").strip():
        raise ExceptionError("say why (or what) in the note")
    measured, pictures = _reading(reading), _photos(photos)
    client_id = (str(client_id).strip()[:80] or None) if client_id else None
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:  # the field reports on the event, which its host carries
        return report(conn, row["joined_to"], by, status, note, reading=reading, photos=photos, client_id=client_id,
                      at=at, override=override)
    if client_id and any(f.get("client_id") == client_id for f in (row["evidence"] or {}).get("field") or []):
        return row  # already here: the phone sent it again after losing the answer
    unpermitted = None
    if status == "on_site":
        from . import readiness

        permit = readiness.permits(conn, list(row["subjects"] or []), row["id"])
        if not permit["ready"]:
            override = (override or "").strip()
            if not override:
                raise ExceptionError(f"{permit['blockers'][0]}. Say why it starts anyway.")
            gates.require_person(conn, "field_start_without_permit", by, item_id=row["item_id"],
                                 ref=f"exception:{row['id']}")
            unpermitted = permit["blockers"][0]
    made, late = _when(conn, at)
    entry = {"at": made, "by": by, "status": status, "note": note}
    if unpermitted:
        entry["without_permit"] = {"blocker": unpermitted, "why": override}
    if client_id:
        entry["client_id"] = client_id
    if late:
        entry["received_at"] = conn.execute("select now() as t").fetchone()["t"].isoformat()
    if measured:
        entry["reading"] = measured
    if pictures:
        entry["photos"] = [conn.execute("insert into field_photo (exception_id, by, mime, data) values (%s, %s, %s, %s) "
                                        "returning id", (exception_id, by, m, d)).fetchone()["id"] for m, d in pictures]
    conn.execute("update exception_record set evidence = jsonb_set(evidence, '{field}', "
                 "coalesce(evidence->'field', '[]') || %s), updated_at = now() where id = %s",
                 (Jsonb([entry]), exception_id))
    if row["item_id"]:
        said = {"on_site": "On site", "done": "Done", "could_not_do": "Could not do", "note": "Note"}[status]
        extra = [reading_text(measured)] if measured else []
        if pictures:
            extra.append(f"{len(pictures)} photo{'s' if len(pictures) > 1 else ''}")
        if late:
            extra.append(f"made {made[:16].replace('T', ' ')}, sent when back in signal")
        text = f"Field: {said}" + (f": {note}" if note else "") + (f" ({'; '.join(extra)})" if extra else "")
        work.comment(conn, row["item_id"], by, text, [
            {"kind": "photo", "exception_id": exception_id, "photo_id": i} for i in entry.get("photos", [])])
        if unpermitted:
            work.comment(conn, row["item_id"], by, f"Started without a valid permit ({unpermitted}). Why: {override}")
        from . import routing

        # a queue_item case (#509) is a plain queue case run as a process: Done closes it as before
        has_process = conn.execute("select 1 from work_process where item_id = %s and process_key <> %s",
                                   (row["item_id"], routing.QUEUE_PROCESS)).fetchone()
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
                came = (row["evidence"] or {}).get("handover") or {}
                # handed over at shift change (P5.11c): back past the outgoing person to whoever sent it to them
                sender = (came.get("accepted_at") and came.get("to") == by
                          and _sent(conn, row["item_id"], came["from"]))
                if sender and sender["by"] and not gates.machine(sender["by"]):
                    work.dispatch(conn, row["item_id"], by, sender["by"], back)
                else:
                    work.hand_back(conn, row["item_id"], by, by, reason=back)
    row = _row(conn, exception_id)
    from . import closeout

    closeout.on_field_report(conn, row, by, status, note)  # the system that raised it hears what the field did (#466)
    events.publish(conn, "exception.reported", f"exception:{row['id']}", {**_payload(row), **entry})
    return row


# SAP's own status and notification type codes, as words (#494); the code stays beside them.
def _sent(conn, item_id: int, person: str) -> dict | None:
    """Who sent this job to `person`, when, and what they asked for: the latest hand-off to them or to a queue they
    took it from, else the case being opened for them or that queue (taking it isn't being sent it)."""
    return conn.execute(
        """
        select actor as by, at, data->>'reason' as reason from work_event
        where item_id = %(i)s and ((kind = 'handed_off' and actor <> %(p)s
                                    and (data->>'to' = %(p)s or data->>'to' like 'role:%%'))
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
               or (t.id is null and w.assignee is null and w.role = any(%(roles)s))
               or (e.evidence->'handover'->>'to' = %(p)s and e.evidence->'handover'->>'accepted_at' is null))
        order by coalesce(e.evidence->'handover'->>'to' = %(p)s and e.evidence->'handover'->>'accepted_at' is null,
                          false) desc,
                 coalesce(t.assignee = %(p)s, false) or (t.id is null and coalesce(w.assignee = %(p)s, false)) desc,
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
        on = conn.execute("select * from exception_record where item_id = %s and state = 'open' "
                          "order by joined_to nulls first, first_seen", (r["item_id"],)).fetchall()
        shown = dict(zip([x["id"] for x in on], _findings(conn, on), strict=True))
        mine = shown.get(r["id"]) or _findings(conn, [r])[0]
        # The checks before the work (a compressor trip's restart checklist), from every source on the case
        checklist = list(dict.fromkeys(c for f in shown.values() for c in f["checks"]))
        out.append({**r, "label": t.label if t else r["type"].replace("_", " "), "assets": _assets(conn, subjects),
                    "checklist": checklist,
                    "sources": sources if len(sources) > 1 else [],
                    "finding": mine, "history": history, "field": r["evidence"].get("field", []),
                    "sent": sent, "permit": _permit(conn, subjects, r["id"]),
                    "warnings": _warnings(conn, subjects, r["id"]),
                    "handover": _handover_for(r, person)})
    return out



def _handover_for(row: dict, person: str) -> dict | None:
    """A handover of this job still waiting to be accepted (P5.11c), as the person sees it: theirs to accept, or
    theirs, handed over and still with them until it is."""
    h = (row.get("evidence") or {}).get("handover")
    if not h or h.get("accepted_at"):
        return None
    return {**h, "mine_to_accept": h.get("to") == person, "waiting_on": h.get("to") if h.get("from") == person else None}


def _holder(conn, row: dict) -> str | None:
    """Who has a job now: its case's open person step's assignee, else the case's."""
    step = conn.execute("select assignee from work_token where item_id = %s and kind = 'user' "
                        "and state in ('ready', 'waiting') order by due_at nulls last, id limit 1",
                        (row["item_id"],)).fetchone()
    if step and step["assignee"]:
        return step["assignee"]
    item = conn.execute("select assignee from work_item where id = %s", (row["item_id"],)).fetchone()
    return item and item["assignee"]


def hand_over(conn, exception_id: int, by: str, to: str, done: str, left: str, unsafe: str | None = None) -> dict:
    """Hand a job in progress to someone on the incoming crew (P5.11c): what was done, what is left and anything
    unsafe to leave, with the permit and isolations it runs under as they stand. It stays with `by` until `to`
    accepts it; it shows at the top of `to`'s jobs meanwhile."""
    from ..core import gates

    gates.require_person(conn, "field_dispatch", by, ref=f"exception:{exception_id}", what="handing a job over")
    done, left, unsafe = (done or "").strip(), (left or "").strip(), (unsafe or "").strip() or None
    if not done or not left:
        raise ExceptionError("say what was done and what is left")
    to = (to or "").strip()
    if not to or to == by or to.startswith(("role:", "agent:")):
        raise ExceptionError("hand it to a person on the incoming crew")
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:
        return hand_over(conn, row["joined_to"], by, to, done, left, unsafe)
    if row["state"] != OPEN or not row["item_id"]:
        raise ExceptionError(f"exception {exception_id} is not a job in the field")
    if _holder(conn, row) != by:
        raise ExceptionError("only whoever has the job hands it over")
    permit = _permit(conn, list(row["subjects"] or []), row["id"])
    h = {"from": by, "to": to, "at": conn.execute("select now() as t").fetchone()["t"].isoformat(), "done": done,
         "left": left, "unsafe": unsafe, "permit": permit and permit["permit"], "accepted_at": None}
    conn.execute("update exception_record set evidence = jsonb_set(evidence, '{handover}', %s), updated_at = now() "
                 "where id = %s", (Jsonb(h), row["id"]))
    work.comment(conn, row["item_id"], by, f"Handover to {to}: done: {done}; left: {left}"
                 + (f"; unsafe to leave: {unsafe}" if unsafe else "") + ". Stays with " + by + " until accepted.")
    events.publish(conn, "exception.handover", f"exception:{row['id']}", {**_payload(row), "from": by, "to": to})
    return _row(conn, row["id"])


def accept_handover(conn, exception_id: int, by: str) -> dict:
    """The incoming person takes the job handed to them (P5.11c): it moves to them, and who and when is kept."""
    row = _row(conn, exception_id, lock=True)
    if row["joined_to"]:
        return accept_handover(conn, row["joined_to"], by)
    h = (row["evidence"] or {}).get("handover")
    if not h or h.get("accepted_at"):
        raise ExceptionError("nothing is waiting to be handed over on this job")
    if h["to"] != by:
        raise ExceptionError(f"this handover is for {h['to']}")
    work.dispatch(conn, row["item_id"], h["from"], by, f"handover: {h['left']}")
    h = {**h, "accepted_at": conn.execute("select now() as t").fetchone()["t"].isoformat()}
    conn.execute("update exception_record set evidence = jsonb_set(evidence, '{handover}', %s) "
                 "|| jsonb_build_object('handovers', coalesce(evidence->'handovers', '[]') || %s), updated_at = now() "
                 "where id = %s", (Jsonb(h), Jsonb([h]), row["id"]))
    work.comment(conn, row["item_id"], by, f"Accepted the handover from {h['from']}")
    events.publish(conn, "exception.handover_accepted", f"exception:{row['id']}",
                   {**_payload(row), "from": h["from"], "to": by})
    return _row(conn, row["id"])


def _warnings(conn, subjects: list[str], exception_id: int) -> list[str]:
    """What the person doing the job should know before starting that never holds it: an open MOC on the equipment."""
    from . import readiness

    return readiness.mocs(conn, subjects, exception_id)["warnings"]


def _permit(conn, subjects: list[str], exception_id: int) -> dict | None:
    """The permit to work a field job runs under (P5.11a): the valid one, else the nearest, with its isolations, and
    whether starting is blocked. None when no permit records are loaded."""
    from . import readiness

    p = readiness.permits(conn, subjects, exception_id)
    if not p["checked"]:
        return None
    shown = next((x for x in p["permits"] if x["valid"]), p["permits"][0] if p["permits"] else None)
    return {"needed": p["needed"], "blocked": not p["ready"], "blocker": p["blockers"][0] if p["blockers"] else None,
            "permit": shown and {k: shown[k] for k in ("label", "type", "state", "valid_from", "valid_to", "work",
                                                       "location_label", "valid", "why_not", "isolations")}}


# ---- exception types as data: other systems' exceptions without code ------------------------------------------

def define(conn, name: str, by: str, label: str, role: str | None = None, process: str | None = None,
           case_type: str | None = None, audience=("ioc",), close_case_on_clear: bool = True,
           description: str | None = None, joins: bool = True, source_system: str | None = None,
           due_hours: str | None = None) -> dict:
    """Define (or change) an exception type as data, so another system (SAP notifications, SCADA alarms, a
    reporting app) can raise it over the API. Types modules declare in code can't be redefined here.
    `source_system` is the system whose source pack brought it (#465): that system's webhook may raise it.
    `due_hours` is its due policy, priority to hours (`1:24,2:72`): a dispatched case is due that long after it was
    raised, by the priority its evidence gives (#509)."""
    from ..core.exceptions import AUDIENCES

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name or ""):
        raise ExceptionError("a type name is lower case letters, digits and _ (sap_notification, say)")
    if name in TYPES:
        raise ExceptionError(f"{name} is declared in code ({TYPES[name].label}); it can't be redefined as data")
    if bad := set(audience) - set(AUDIENCES):
        raise ExceptionError(f"audience must be among {AUDIENCES}, not {sorted(bad)}")
    if not (label or "").strip():
        raise ExceptionError("a type needs a label")
    due_hours = (due_hours or "").strip() or None
    if due_hours and not re.fullmatch(r"\s*[^:,\s]+\s*:\s*\d+(\.\d+)?\s*(,\s*[^:,\s]+\s*:\s*\d+(\.\d+)?\s*)*", due_hours):
        raise ExceptionError("due hours map a priority to hours, comma separated: 1:24,2:72")
    if role:  # a queue nobody can be in would hold its cases where no Inbox shows them
        queue = role.removeprefix("role:")
        known = work.person_roles(conn)
        if queue not in known:
            raise ExceptionError(f"no role queue '{queue}'; pick one of {', '.join(known)} (or add the role on Teams)")
        role = f"role:{queue}"
    return conn.execute(
        """
        insert into exception_kind (name, label, role, process, case_type, audience, close_case_on_clear, description,
                                    updated_by, joins, source_system, due_hours)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (name) do update set label = excluded.label, role = excluded.role, process = excluded.process,
            case_type = excluded.case_type, audience = excluded.audience,
            close_case_on_clear = excluded.close_case_on_clear, description = excluded.description, joins = excluded.joins,
            source_system = coalesce(excluded.source_system, exception_kind.source_system), due_hours = excluded.due_hours,
            updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (name, label.strip(), role or None, process or None, case_type or None, list(audience), close_case_on_clear,
              description, by, joins, source_system or None, due_hours)).fetchone()


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

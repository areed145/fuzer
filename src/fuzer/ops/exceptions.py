"""The built-in exception implementation behind fuzer.core.exceptions: one `exception_record` per open condition,
matched to entities, routed to a case through the Work port, and published on the event outbox.

    raise_(conn, type, key, title, by, refs=..., priority=..., value_usd=..., evidence=..., source=..., assignee=...)
    clear(conn, type, key, by, reason)
    reconcile(conn)          close the ones whose case finished (the work tick runs it)
    match(conn, refs)        -> (fuzer ids, refs that matched nothing)
    listing(conn, ...)       what the IOC (or the field) sees

States: open (the condition holds), cleared (it went away) and closed (its case finished while it held; the next
raise opens a new exception and a new case, so a closed case never hides a condition that came back).
"""

import re

from psycopg.types.json import Jsonb

from ..core import events, work
from ..core.exceptions import TYPES, ExceptionError, ExceptionType

OPEN = "open"


KIND_FIELDS = ("label", "role", "process", "case_type", "audience", "close_case_on_clear", "description")


def types(conn) -> dict[str, ExceptionType]:
    """Every exception type: the ones a deployment defined as data (exception_kind, for other systems' exceptions)
    and the ones modules declare in code, which win on a clash."""
    data = {r["name"]: ExceptionType(r["name"], r["label"], role=r["role"], process=r["process"],
                                     case_type=r["case_type"], audience=tuple(r["audience"]),
                                     close_case_on_clear=r["close_case_on_clear"], description=r["description"] or "")
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
        if found:
            ids += [f for f in sorted(found) if f not in ids]
        else:
            unmatched.append(ref)
    return ids, unmatched


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
            "item_id", "raised_count")
    out = {k: row[k] for k in keep}
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
        if t.process:  # new evidence for the process already working it
            _case(conn, t, {**row, "evidence": evidence or {}}, by, assignee, role)
        return {**row, "raised": False, "unmatched_note": _unmatched_note(conn, unmatched)}
    row = conn.execute(
        """
        insert into exception_record (type, key, title, source, priority, value_usd, subjects, unmatched, evidence)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (type, key, title, source or by, priority, value_usd, subjects, unmatched, Jsonb(evidence or {}))).fetchone()
    # The work engine runs each operation as its own transaction: a case that can't open rolls this insert back
    # with it, and one that opens commits it.
    item_id = _case(conn, t, row, by, assignee, role)
    if item_id:
        row = conn.execute("update exception_record set item_id = %s where id = %s returning *",
                           (item_id, row["id"])).fetchone()
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
    if row["item_id"] and t.close_case_on_clear and not t.process:
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
    planned if the IOC put it on the integrated plan instead of working it now."""
    names = [n for n, t in types(conn).items() if audience is None or audience in t.audience]
    return conn.execute(
        """
        select e.*, w.state as case_state, w.assignee, w.role,
               (select json_build_object('id', p.id, 'start_at', p.start_at, 'end_at', p.end_at, 'crew', p.crew)
                from plan_item p where p.exception_id = e.id and p.status in ('proposed', 'scheduled', 'in_progress')
                order by p.start_at limit 1) as planned
        from exception_record e
        left join work_item w on w.id = e.item_id
        where (%(state)s::text is null or e.state = %(state)s) and (%(type)s::text is null or e.type = %(type)s)
          and (%(subject)s::text is null or %(subject)s = any(e.subjects))
          and (%(all)s or e.type = any(%(types)s))
        order by e.value_usd desc nulls last, e.priority desc nulls last, e.last_seen desc limit %(limit)s
        """, {"state": state, "type": type, "subject": subject, "all": audience is None, "types": names,
              "limit": limit}).fetchall()


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


def dispatch(conn, exception_id: int, by: str, to: str, reason: str | None = None) -> dict:
    """Send an exception to someone (a person, `role:<role>`): open its case for them if it has none, else hand
    its case (or the case's open person step) to them. Publishes exception.dispatched."""
    row = _row(conn, exception_id, lock=True)
    if row["state"] != OPEN:
        raise ExceptionError(f"exception {exception_id} is {row['state']}")
    t = types(conn).get(row["type"]) or ExceptionType(row["type"], row["type"])
    if row["item_id"]:
        work.dispatch(conn, row["item_id"], by, to, reason)
    else:
        item_id = work.open_case(conn, t.case_type or t.name, row["title"], by, subjects=row["subjects"],
                                 priority=row["priority"], assignee=None if to.startswith("role:") else to,
                                 role=to.removeprefix("role:") if to.startswith("role:") else None, source=row["source"],
                                 payload={**row["evidence"], "exception_id": row["id"]}, reason=reason)["id"]
        conn.execute("update exception_record set item_id = %s, updated_at = now() where id = %s", (item_id, row["id"]))
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
    events.publish(conn, "exception.reported", f"exception:{row['id']}", {**_payload(row), **entry})
    return row


def _seen(evidence: dict) -> list[tuple[str, str]]:
    """The evidence a person reads at a glance: short scalar values, not the nested detail."""
    out = []
    for k, v in evidence.items():
        if k in SEEN_SKIP or isinstance(v, (dict, list)) or v is None or v == "":
            continue
        if isinstance(v, bool):
            text = "yes" if v else "no"
        elif isinstance(v, float):
            text = f"{v:g}"
        else:
            text = str(v)[:120]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", text):
                text = text.replace("T", " ")[:16]
        label = "assets affected" if k == "units" and isinstance(v, int) else SEEN_LABELS.get(k, k.replace("_", " "))
        out.append((label, text))  # an upset's `units` counts assets; another source's names a unit of measure
    return out[:12]


def _sent(conn, item_id: int, person: str) -> dict | None:
    """Who sent this job to `person`, when, and what they asked for: the latest hand-off to them, else the case
    being opened for them."""
    return conn.execute(
        """
        select actor as by, at, data->>'reason' as reason from work_event
        where item_id = %(i)s and ((kind = 'handed_off' and data->>'to' = %(p)s)
                                   or (kind = 'created' and data->>'assignee' = %(p)s))
        order by id desc limit 1
        """, {"i": item_id, "p": person}).fetchone()


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
               t.id as task_id, t.name as task_name, t.role as task_role, p.data->>'field_ask' as ask,
               not (coalesce(t.assignee = %(p)s, false) or (t.id is null and coalesce(w.assignee = %(p)s, false)))
                 as queued
        from exception_record e join work_item w on w.id = e.item_id
        left join lateral (select id, name, due_at, assignee, role from work_token
                           where item_id = w.id and kind = 'user' and state in ('ready', 'waiting')
                           order by due_at nulls last, id limit 1) t on true
        left join work_process p on p.id = (select process_id from work_token where id = t.id)
        where e.state = 'open' and w.state not in ('done', 'cancelled')
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
            sent = {**(sent or {"by": None, "at": None}), "reason": r["ask"]}
        history = conn.execute(
            "select id, title, state, first_seen from exception_record where id <> %s and subjects && %s "
            "and first_seen > now() - interval '90 days' order by first_seen desc limit 5",
            (r["id"], r["subjects"])).fetchall() if r["subjects"] else []
        t = known.get(r["type"])
        out.append({**r, "label": t.label if t else r["type"].replace("_", " "), "assets": _assets(conn, r["subjects"]),
                    "seen": _seen(r["evidence"]), "history": history, "field": r["evidence"].get("field", []),
                    "sent": sent})
    return out



# ---- exception types as data: other systems' exceptions without code ------------------------------------------

def define(conn, name: str, by: str, label: str, role: str | None = None, process: str | None = None,
           case_type: str | None = None, audience=("ioc",), close_case_on_clear: bool = True,
           description: str | None = None) -> dict:
    """Define (or change) an exception type as data, so another system (SAP notifications, SCADA alarms, a
    reporting app) can raise it over the API. Types modules declare in code can't be redefined here."""
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
                                    updated_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (name) do update set label = excluded.label, role = excluded.role, process = excluded.process,
            case_type = excluded.case_type, audience = excluded.audience,
            close_case_on_clear = excluded.close_case_on_clear, description = excluded.description,
            updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (name, label.strip(), role or None, process or None, case_type or None, list(audience), close_case_on_clear,
              description, by)).fetchone()


def undefine(conn, name: str) -> bool:
    """Remove a type defined as data; refused while any exception of it is open."""
    if conn.execute("select 1 from exception_record where type = %s and state = 'open' limit 1", (name,)).fetchone():
        raise ExceptionError(f"{name} has open exceptions; clear them first")
    return conn.execute("delete from exception_kind where name = %s", (name,)).rowcount > 0


def type_listing(conn) -> list[dict]:
    """Every type, where it is defined (code or data), and how many of it are open."""
    open_n = {r["type"]: r["n"] for r in conn.execute(
        "select type, count(*) as n from exception_record where state = 'open' group by type")}
    return [{"name": n, "label": t.label, "role": t.role, "process": t.process, "case_type": t.case_type,
             "audience": list(t.audience), "close_case_on_clear": t.close_case_on_clear, "description": t.description,
             "defined_in": "code" if n in TYPES else "data", "open": open_n.get(n, 0)}
            for n, t in sorted(types(conn).items())]


@events.subscriber("exception.dispatched")
def tell_who_was_sent(conn, event: dict) -> None:
    """Post a dispatch to the alert webhook (Teams / Slack), naming who was sent, so a field operator hears of it
    without watching the app."""
    from ..sources import alerts

    e = event["payload"]
    who = e.get("to") or ""
    alerts.post(f"Dispatched to {who.removeprefix('role:')}: {e.get('title')}"
                + (f" ({e['reason']})" if e.get("reason") else ""))

"""Corrections flow back to the source (P2.4): when a steward's answer says a source is wrong, the fix is drafted for
that source's owner, and what comes back closes back to the steward.

An answer on Master data's Questions tab records which side it says is wrong (graph.review): the systems whose value
differs from the one chosen, the drawing (the system is right), the register or alarm configuration (the drawing is
right), or nobody yet ("check it in the field"). For each, `sync` drafts one correction:

    master_data          a change to that system's record, through the approved write-back queue (ops.writeback),
                         in its approver's queue; a system declared not to take them gets a case for its owner
    drawing_correction   a redline for the drawing's owner, through the same queue
    correction           a case for the owner of something no write-back reaches (the configured alarms)
    field_check          a case for the operators: look at the plant and say what is there

The original question stays answered while its correction is out, so the next ingest doesn't ask it again. It comes
back to the steward when the correction does:

    done       the source took it (its write-back confirmed), or the source now says what the steward chose
    returned   the owner rejected it, the source changed to something else, or the field check came back: the
               question reopens on the Questions tab, and as a case in the steward's queue, with what happened

    sync(conn)        draft what new answers call for, and settle what came back; runs after answers and rebuilds
    listing(conn)     the corrections, newest first
"""

from psycopg.types.json import Jsonb

from ..core import work
from . import routing, writeback

BY = "stewardship"
FIELD_CASE = "field_check"
OWNER_CASE = "source_correction"


def _norm(v) -> str:
    return "".join(ch for ch in str(v if v is not None else "").lower() if ch.isalnum())


def _label(conn, fid: str | None) -> str:
    row = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone() if fid else None
    return row["label"] if row else (fid or "")


def _round(item: dict) -> int:
    """Which answer this is: a question answered again after its correction came back drafts a new one."""
    return int(item["decided_at"].timestamp()) if item.get("decided_at") else 0


def _owner(conn, system: str) -> dict:
    row = conn.execute("select owner, steward from source_system where name = %s", (system,)).fetchone()
    person = (row or {}).get("owner") or (row or {}).get("steward")
    return {"assignee": person} if person else {"role": "role:data_steward"}


def _insert(conn, item: dict, kind: str, system: str, **kw) -> dict:
    return conn.execute(
        "insert into source_correction (review_item_id, kind, system, fuzer_id, field, wrong, right_value, "
        "writeback_id, item_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "returning *",
        (item["id"], kind, system, kw.get("fuzer_id"), kw.get("field"), Jsonb(kw.get("wrong")),
         Jsonb(kw.get("right")), kw.get("writeback_id"), kw.get("item_id"))).fetchone()


def _write(conn, item: dict, system: str, kind: str, payload: dict, title: str, by: str, subjects: list) -> dict:
    """Through the write-back queue when the system takes this kind; else a case for its owner."""
    if kind in writeback.policy(conn, system)["kinds"]:
        wb = writeback.propose(conn, system, kind, payload, by, record=payload.get("record"),
                               key=f"correction:{item['id']}:{system}:{kind}:{_round(item)}", title=title, subjects=subjects,
                               source_ref=f"review:{item['id']}")
        return {"writeback_id": wb["id"], "item_id": wb["item_id"]}
    case = routing.open_task(conn, OWNER_CASE, f"Correct in {system.upper()}: {title}", by,
                             f"correction:{item['id']}:{system}:{_round(item)}", data={"review_item": item["id"], **payload},
                             subjects=subjects, source=system, **_owner(conn, system))
    return {"item_id": case["id"]}


def _draft(conn, item: dict) -> int:
    """The corrections one answer calls for. Returns how many were drafted."""
    d, p, n = item["decision"] or {}, item["payload"] or {}, 0
    by = item["decided_by"] or BY
    fid = p.get("fuzer_id")
    subjects = [fid] if fid else []
    what = _label(conn, fid)
    if d.get("decision") == "field":
        title = (f"Check {what}'s {p['field']} in the field" if item["kind"] == "conflict"
                 else f"Check {p.get('tag') or what} in the field against {p.get('sheet') or 'the drawing'}")
        case = routing.open_task(conn, FIELD_CASE, title, by, f"field_check:{item['id']}:{_round(item)}", role="role:operator",
                                 data={"review_item": item["id"], "question": p.get("question") or title},
                                 subjects=subjects, source=BY)
        return 1 if _insert(conn, item, "field_check", "", fuzer_id=fid, field=p.get("field"),
                            item_id=case["id"]) else 0
    for system in d.get("wrong") or []:
        if item["kind"] == "conflict":
            v = next((x for x in p.get("values") or [] if x.get("source") == system), None)
            if v is None:
                continue
            payload = {"entity": what, "field": p["field"], "from": v.get("value"), "to": d.get("right"),
                       "record": v.get("source_key")}
            title = f"{what} {p['field']}: {v.get('value')} should be {d.get('right')}"
            out = _write(conn, item, system, "master_data", payload, title, by, subjects)
            row = _insert(conn, item, "master_data", system, fuzer_id=fid, field=p["field"], wrong=v.get("value"),
                          right=d.get("right"), **out)
        elif system == "drawing":
            payload = {"sheet": p.get("sheet"), "document": p.get("document"), "tag": p.get("tag"),
                       "detail": p.get("detail"), "record": p.get("sheet")}
            title = f"Redline {p.get('sheet')}: {p.get('detail')}"
            out = _write(conn, item, "pid", "drawing_correction", payload, title, by, subjects)
            row = _insert(conn, item, "drawing_correction", "pid", fuzer_id=fid, wrong=p.get("detail"), **out)
        elif system == "alarm configuration":
            title = f"{p.get('tag')}: the configured alarm should match {p.get('sheet')} ({p.get('detail')})"
            case = routing.open_task(conn, OWNER_CASE, title, by, f"correction:{item['id']}:alarms:{_round(item)}",
                                     role="role:data_steward", data={"review_item": item["id"]}, subjects=subjects,
                                     source=BY)
            row = _insert(conn, item, "correction", "alarms", fuzer_id=fid, wrong=p.get("detail"), item_id=case["id"])
        elif p.get("check") == "flow":  # the flow network links what the drawings don't, or misses what they do
            payload = {"from": p.get("from"), "to": p.get("to"), "detail": p.get("detail"),
                       "drawn_path": p.get("drawn_path"), "record": p.get("tag")}
            title = f"Flow link {p.get('from')} to {p.get('to')} should match {p.get('sheet')}: {p.get('detail')}"
            out = _write(conn, item, system, "master_data", payload, title, by, subjects)
            row = _insert(conn, item, "master_data", system, fuzer_id=fid, wrong=p.get("detail"),
                          right=p.get("drawn_path"), **out)
        else:  # a register that has the instrument under another item than the drawing shows
            payload = {"tag": p.get("tag"), "detail": p.get("detail"), "drawn_on": p.get("drawn_on"),
                       "record": p.get("tag")}
            title = f"{p.get('tag')} belongs under {p.get('drawn_on')} (as {p.get('sheet')} shows)"
            out = _write(conn, item, system, "master_data", payload, title, by, subjects)
            row = _insert(conn, item, "master_data", system, fuzer_id=fid, wrong=p.get("detail"),
                          right=p.get("drawn_on"), **out)
        n += 1 if row else 0
    return n


def _note(conn, item_id: int | None) -> str | None:
    """What the person who closed a case said last."""
    if not item_id:
        return None
    row = conn.execute("select data from work_event where item_id = %s and kind in ('commented', 'state_changed', "
                       "'completed') order by id desc limit 1", (item_id,)).fetchone()
    data = (row or {}).get("data") or {}
    return data.get("text") or data.get("reason") or data.get("note")


def _steward_case(conn, review_item_id: int) -> int | None:
    row = conn.execute("select x.item_id from review_item r join exception_record x on x.key = r.kind || ':' || "
                       "r.subject where r.id = %s and x.item_id is not null order by x.id desc limit 1",
                       (review_item_id,)).fetchone()
    return row["item_id"] if row else None


def _settle(conn, c: dict, state: str, outcome: str, by: str) -> None:
    conn.execute("update source_correction set state = %s, outcome = %s, closed_at = now() where id = %s",
                 (state, outcome, c["id"]))
    if state == "returned":  # back to the steward: the question reopens, with what happened
        conn.execute("update review_item set status = 'open', payload = payload || %s where id = %s",
                     (Jsonb({"returned": {"kind": c["kind"], "system": c["system"], "why": outcome}}),
                      c["review_item_id"]))
    elif case := _steward_case(conn, c["review_item_id"]):
        work.comment(conn, case, by, outcome)


def _check(conn, c: dict) -> tuple[str, str] | None:
    """What came back for an open correction: (state, outcome), or None while it is still out."""
    if c["writeback_id"]:
        wb = conn.execute("select state, reason, error from writeback where id = %s", (c["writeback_id"],)).fetchone()
        if wb and wb["state"] == "confirmed":
            return "done", f"{c['system'].upper()} took the correction."
        if wb and wb["state"] == "rejected":
            return "returned", (f"{c['system'].upper()}'s owner rejected the correction"
                                + (f": {wb['reason']}" if wb["reason"] else "") + ". Look at it again.")
    if c["kind"] == "master_data" and c["field"] and c["fuzer_id"]:  # what the source says since the last load
        says = [r["value"] for r in conn.execute("select value from reviewed_value where fuzer_id = %s and field = %s "
                                                 "and source = %s", (c["fuzer_id"], c["field"], c["system"]))]
        norm = {_norm(v) for v in says}
        if says and _norm(c["wrong"]) not in norm:
            if norm == {_norm(c["right_value"])}:
                return "done", f"{c['system'].upper()} now says {says[0]}, as the steward chose."
            return "returned", (f"{c['system'].upper()} changed it to {', '.join(map(str, says))}, neither what it "
                                f"said nor what was chosen. Look at it again.")
    if not c["writeback_id"] and c["item_id"]:
        item = conn.execute("select state from work_item where id = %s", (c["item_id"],)).fetchone()
        if item and item["state"] in ("done", "cancelled"):
            said = _note(conn, c["item_id"])
            if c["kind"] == "field_check":
                return "returned", "Checked in the field" + (f": {said}" if said else "") + ". Answer it now."
            return ("done" if item["state"] == "done" else "returned"), (
                f"{c['system'].upper()}'s owner {'made' if item['state'] == 'done' else 'declined'} the correction"
                + (f": {said}" if said else "") + ".")
    return None


def sync(conn, by: str = BY) -> dict:
    """Draft what new answers call for and settle what came back. Returns {drafted, done, returned}."""
    out = {"drafted": 0, "done": 0, "returned": 0}
    for item in conn.execute(
            "select * from review_item r where r.status = 'decided' and r.kind in ('conflict', 'pid_mismatch') "
            "and (r.decision ? 'wrong' or r.decision->>'decision' = 'field') "
            "and not exists (select 1 from source_correction c where c.review_item_id = r.id "
            "and c.created_at >= r.decided_at) order by r.id").fetchall():
        out["drafted"] += _draft(conn, item)
    for c in conn.execute("select * from source_correction where state = 'open' order by id").fetchall():
        settled = _check(conn, c)
        if settled:
            _settle(conn, c, *settled, by)
            out[settled[0]] += 1
    return out


def listing(conn, review_item_id: int | None = None, limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select c.*, w.state as writeback_state from source_correction c left join writeback w on w.id = c.writeback_id "
        "where %(r)s::bigint is null or c.review_item_id = %(r)s order by c.id desc limit %(n)s",
        {"r": review_item_id, "n": limit})]

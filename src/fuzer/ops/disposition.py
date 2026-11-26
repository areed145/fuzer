"""Structured close-out (P6.1a, #655): closing a case says what it was and, for a real problem, what failed, why and
what was done, in the ISO 14224 terms of `standards/iso14224.yaml`, so the closed work can be counted and compared.

    real          a real problem: failure mode, cause and action are required; mechanism and outcome are kept
    false_alarm   nothing was wrong
    duplicate     another case already covers it (named by `duplicate_of`)
    data_problem  the data was wrong, not the equipment
    done          done as asked: only for work someone asked for (no exception opened it), such as a task
    log_only      log only, no action: only for a message to the IOC that turned out to say what was done (#1363)
    matched       a steward's question answered by confirming a match or choosing the entity (set by the answer)
    no_work       a message the steward said needed no work (set by the answer)

The form is pre-filled from an agent's suggestion on the case (`payload.closeout_suggestion`), else from the case's
own words: the failure-mode keywords of the taxonomy and pack, and the action words below. The person confirms it.

Re-check and repeats (P6.1b, #656): a real problem's close-out sets a re-check `closeout.recheck_days` later. When it
is due, whoever closed it gets a "did the fix hold?" case; a no reopens the first case as a repeat. A new case on the
same asset with the same failure mode inside the window does the same: the first case reopens, linked to the new one,
and the new one is closed into it instead of starting fresh. `sweep` does both, every minute.
"""

import re

from psycopg.types.json import Jsonb

from ..core import settings, work
from ..core.exceptions import exception_type
from ..core.pack import Pack
from ..core.taxonomy import failure_modes_in, load_standard
from . import routing

DISPOSITIONS = {"real": "A real problem", "false_alarm": "False alarm", "duplicate": "Duplicate",
                "data_problem": "Data problem", "done": "Done as asked", "log_only": "Log only, no action",
                "matched": "Matched", "no_work": "No work needed"}
# what a steward's answer closes a data question as (graph.data_work.closeout), never picked on the close-out form
ANSWERED = ("matched", "no_work")
OUTCOMES = {"fixed": "Fixed", "contained": "Contained, follow-up planned", "not_fixed": "Not fixed",
            "monitoring": "Being monitored"}
# Outcomes that leave work to do: each sends a follow-up, as a case of its own (#1385; not fixed too, tour F, F10)
FOLLOWS = ("contained", "not_fixed")
# words that name the maintenance activity (ISO 14224 Table B.5), first match wins
ACTION_WORDS = (("1", r"\breplac"), ("10", r"\boverhaul"), ("2", r"\brepair|\bfix(ed)?\b"), ("4", r"\badjust|\bre-?set\b"),
                ("7", r"\bservic|\bclean|\bgrease|\blubric"), ("8", r"\btest"), ("9", r"\binspect"),
                ("3", r"\bmodif"), ("6", r"\bcheck"))
# the field's own answer ("Field: Done: ...") is a comment, so these read it too (#34)
# "no leak or damage found" is nothing found, though it names a leak (#1400)
FALSE_WORDS = (r"\bfalse alarm\b|\bspurious\b|\bnothing (was )?(wrong|found)\b|\bno fault\b|(?<!not )\bas it should be\b"
               r"|\ball (good|ok|normal)\b|\bno [a-z]+( or [a-z]+)? (was |were )?found\b")
DATA_WORDS = (r"\bbad (data|value|reading)\b|\bstale\b|\bwrong unit|\bsensor (fault|drift)\b|\bdata (problem|issue)\b"
              r"|\bimpulse line\b|\b(agrees|agreed|matches|matched) with the (dcs|scada|local gauge)\b|\brecalibrat"
              r"|\breads? wrong\b|\breading wrong\b")


def vocabulary(pack: Pack) -> dict:
    """The controlled lists the form picks from: dispositions, outcomes and the taxonomy's failure terms."""
    std = load_standard(pack.classification.get("taxonomy", "iso14224"))
    return {"dispositions": DISPOSITIONS, "answered": list(ANSWERED), "outcomes": OUTCOMES,
            "failure_modes": std.get("failure_modes", {}),
            "mechanisms": std.get("failure_mechanisms", {}), "causes": std.get("failure_causes", {}),
            "activities": std.get("maintenance_activities", {})}


def _text(conn, item_id: int) -> tuple[dict, str]:
    item = conn.execute("select id, title, payload, closed_at, state from work_item where id = %s", (item_id,)).fetchone()
    if not item:
        raise LookupError(f"no case {item_id}")
    notes = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                         "order by id", (item_id,)).fetchall()
    return item, "\n".join([item["title"] or "", *[n["t"] or "" for n in notes]])


def suggest(conn, pack: Pack, item_id: int) -> dict:
    """What to pre-fill: an agent's suggestion on the case if it left one, else what the case's own words say."""
    item, text = _text(conn, item_id)
    agent = (item["payload"] or {}).get("closeout_suggestion")
    if isinstance(agent, dict):
        return {**{k: v for k, v in agent.items() if k in FIELDS}, "from": "agent"}
    if asked_case(conn, item_id):
        return {"from": "words", "disposition": "done"}
    out: dict = {"from": "words"}
    if re.search(FALSE_WORDS, text, re.IGNORECASE):
        out["disposition"] = "false_alarm"
    elif re.search(DATA_WORDS, text, re.IGNORECASE):
        out["disposition"] = "data_problem"
    modes = [m for m in failure_modes_in(text, pack) if m not in ("OTH", "UNK")]
    if modes and "disposition" not in out:
        out |= {"disposition": "real", "failure_mode": modes[0]}
    if out.get("disposition") == "real" and _field_done(conn, item_id):
        out["outcome"] = "fixed"  # the field said Done: what they did fixed it, unless the person says otherwise (#1390)
    for code, pat in ACTION_WORDS:
        if re.search(pat, text, re.IGNORECASE):
            out["activity"] = code
            break
    return out


def _field_done(conn, item_id: int) -> bool:
    """Whether the field's last report on the case (a note after it changes nothing) was Done."""
    row = conn.execute(
        """
        select (select f.r->>'status' from jsonb_array_elements(x.evidence->'field') with ordinality f(r, n)
                where f.r->>'status' <> 'note' order by f.n desc limit 1) as last
        from exception_record x where x.item_id = %s and jsonb_typeof(x.evidence->'field') = 'array'
        """, (item_id,)).fetchall()
    return any(r["last"] == "done" for r in row)


FIELDS = ("disposition", "failure_mode", "mechanism", "cause", "activity", "outcome", "note", "duplicate_of")


def get(conn, item_id: int) -> dict | None:
    return conn.execute("select * from case_disposition where item_id = %s", (item_id,)).fetchone()


def equipment_case(conn, item_id: int) -> bool:
    """Whether a case is about equipment (#1145), so a real problem on it is an equipment failure in ISO 14224 terms:
    it is about a piece of equipment, or a point installed on or part of one, and none of its exceptions is data work
    (a drawing against the DCS, a possible match, a held load: the data steward's types). Elsewhere, such as a
    drawing-vs-alarm limit that turned out real, the failure terms are optional."""
    from . import exceptions

    data = {n for n, t in exceptions.types(conn).items() if t.role == "role:data_steward"}
    if data and conn.execute("select 1 from exception_record where item_id = %s and type = any(%s) limit 1",
                             (item_id, list(data))).fetchone():
        return False
    return conn.execute(
        """
        select 1 from work_item w cross join unnest(w.subjects) s(id)
        where w.id = %(id)s and (exists (select 1 from entity en where en.fuzer_id = s.id and en.kind = 'equipment')
            or exists (select 1 from edge e join entity en on en.fuzer_id = e.dst and en.kind = 'equipment'
                       where e.src = s.id and e.rel in ('installed_at', 'part_of') and e.valid_to is null))
        limit 1
        """, {"id": item_id}).fetchone() is not None


def asked_case(conn, item_id: int) -> bool:
    """Work someone asked for (a task posted to a queue, a draft from the assistant), not a problem an exception or
    the IOC opened: it closes as done as asked, with no failure terms."""
    return conn.execute(
        "select i.type = 'task' and not exists (select 1 from exception_record x where x.item_id = i.id) as x "
        "from work_item i where i.id = %s",
        (item_id,)).fetchone()["x"]


def message_case(conn, item_id: int) -> bool:
    """A case a message to the IOC opened (ops.messages): one that only said what was done closes as log only."""
    return conn.execute("select 1 from exception_record where item_id = %s and type = 'message_report' limit 1",
                        (item_id,)).fetchone() is not None


def form(conn, pack: Pack, item_id: int) -> dict:
    """Everything the close-out form needs: the record if there is one, the pre-fill, the lists, and whether a real
    problem on it needs the equipment-failure terms, and the case it most likely duplicates (`duplicate_of`)."""
    _text(conn, item_id)
    repeats = conn.execute("select again, at from case_repeat where first = %s order by at", (item_id,)).fetchall()
    equipment, suggestion = equipment_case(conn, item_id), suggest(conn, pack, item_id)
    if not equipment:  # the failure terms are optional here, folded away on the form: nothing fills what isn't shown (#1366)
        suggestion = {k: v for k, v in suggestion.items() if k not in TERMS}
    return {"item_id": item_id, "record": get(conn, item_id), "follow_up": follow_up(conn, item_id), "suggestion": suggestion,
            "vocabulary": vocabulary(pack), "repeats": repeats, "equipment": equipment,
            "asked": asked_case(conn, item_id), "message": message_case(conn, item_id),
            "duplicate_of": duplicate_candidate(conn, item_id)}


TERMS = ("failure_mode", "mechanism", "cause", "activity")  # a real problem's equipment-failure terms


def record_known(conn, item_id: int, by: str, disposition: str, note: str | None = None) -> None:
    """A close-out the closing already said (a data question answered on Master data is a data problem, #1145): kept
    unless someone recorded one, so the closed case doesn't ask for it again. Only the kinds that need no failure terms."""
    if disposition not in DISPOSITIONS or disposition in ("real", "duplicate"):
        raise ValueError(f"{disposition} needs the close-out form")
    conn.execute("insert into case_disposition (item_id, disposition, note, by) values (%s, %s, %s, %s) "
                 "on conflict (item_id) do nothing", (item_id, disposition, note, by))


def record(conn, pack: Pack, item_id: int, by: str, disposition: str, *, failure_mode: str | None = None,
           mechanism: str | None = None, cause: str | None = None, activity: str | None = None,
           outcome: str | None = None, note: str | None = None, duplicate_of: int | None = None,
           suggested: dict | None = None, follow_up_to: str | None = None) -> dict:
    """Record the close-out and close the case if it is still open. A real problem on equipment needs its failure mode,
    cause and action from the lists (elsewhere they are optional); a duplicate names its case. Recording again
    corrects it. Only a real problem keeps an outcome (#1400). Contained with a follow-up planned, or not fixed (tour F,
    F10), names `follow_up_to` (a person or `role:<role>`), unless one already went, and it is sent that follow-up as a
    case of its own (#1385); the row says which (`follow_up`). A duplicate adds what it reported, and a link back, to
    the case it duplicates (tour F, F9)."""
    item, _ = _text(conn, item_id)
    if disposition != "real":
        outcome = follow_up_to = None  # an outcome is what came of a real problem's fix: nothing else keeps one (#1400)
    if follow_up_to and outcome not in FOLLOWS:
        raise ValueError("a follow-up is sent when the outcome is contained, follow-up planned, or not fixed")
    if outcome in FOLLOWS and not follow_up_to and not follow_up(conn, item_id):
        raise ValueError(f"{OUTCOMES[outcome].lower()} says who the follow-up goes to")
    v = vocabulary(pack)
    if disposition not in DISPOSITIONS or disposition in ANSWERED:
        raise ValueError(f"say what it was: {', '.join(d for d in DISPOSITIONS if d not in ANSWERED)}")
    if disposition == "done" and not asked_case(conn, item_id):
        raise ValueError("a case an exception opened says what it was: a real problem, a false alarm, a duplicate "
                         "or a data problem")
    if disposition == "log_only" and not message_case(conn, item_id):
        raise ValueError("log only is for a message that only said what was done")
    for name, value, allowed in (("failure mode", failure_mode, v["failure_modes"]), ("mechanism", mechanism, v["mechanisms"]),
                                 ("cause", cause, v["causes"]), ("action", activity, v["activities"]),
                                 ("outcome", outcome, OUTCOMES)):
        if value and str(value) not in {str(k) for k in allowed}:
            raise ValueError(f"{value} is not a {name} on the list")
    if disposition == "real" and equipment_case(conn, item_id):
        missing = [n for n, x in (("failure mode", failure_mode), ("cause", cause), ("action", activity)) if not x]
        if missing:
            raise ValueError(f"a real problem on equipment needs its {', '.join(missing)}")
    elif disposition != "real":
        failure_mode = mechanism = cause = activity = None
    if disposition == "duplicate":
        if not duplicate_of or duplicate_of == item_id or not conn.execute(
                "select 1 from work_item where id = %s", (duplicate_of,)).fetchone():
            raise ValueError("a duplicate names the case it duplicates")
    else:
        duplicate_of = None
    row = conn.execute(
        """
        insert into case_disposition (item_id, disposition, failure_mode, mechanism, cause, activity, outcome, note,
                                      duplicate_of, suggested, by, recheck_at)
        values (%(item_id)s, %(disposition)s, %(failure_mode)s, %(mechanism)s, %(cause)s, %(activity)s, %(outcome)s,
                %(note)s, %(duplicate_of)s, %(suggested)s, %(by)s,
                case when %(disposition)s = 'real' then now() + make_interval(days => %(days)s) end)
        on conflict (item_id) do update set disposition = excluded.disposition, recheck_at = excluded.recheck_at, failure_mode = excluded.failure_mode,
            mechanism = excluded.mechanism, cause = excluded.cause, activity = excluded.activity,
            outcome = excluded.outcome, note = excluded.note, duplicate_of = excluded.duplicate_of,
            suggested = excluded.suggested, by = excluded.by, at = now()
        returning *
        """, {"item_id": item_id, "disposition": disposition, "failure_mode": failure_mode, "mechanism": mechanism,
              "cause": cause, "activity": activity, "outcome": outcome, "note": (note or "").strip() or None,
              "duplicate_of": duplicate_of, "suggested": Jsonb(suggested or {}), "by": by,
              "days": int(settings.get(conn, "closeout.recheck_days"))}).fetchone()
    from . import watch  # a case raised from the watch's proposal: the verdict counts for or against it (P3.11d)

    watch.on_closeout(conn, item_id, disposition, by)
    said = summary(row, v) + _checklist_said(conn, item_id)
    if duplicate_of and item["closed_at"] is None:  # the original carries what the duplicate reported (tour F, F9)
        work.comment(conn, duplicate_of, by, f"Also reported in case {item_id}, closed as a duplicate of this one: "
                                             f"{_reported(conn, item)}")
    if item["closed_at"] is None:
        work.close_case(conn, item_id, "done", by, reason=said)
        conn.execute("update work_item set outcome = %s where id = %s and outcome is null", (summary(row, v), item_id))
        from . import exceptions
        exceptions.reconcile(conn)  # its exception leaves the open list now, not at the next work tick
    else:
        work.comment(conn, item_id, by, f"Close-out: {said}")
    offer_feedback(conn, item_id, row, by)
    return {**row, "follow_up": (_send_follow_up(conn, item, by, follow_up_to, row["note"]) if follow_up_to
                                 else follow_up(conn, item_id))}


def _reported(conn, item: dict) -> str:
    """What a case reported, in its reporter's words: the message it came from, else its title."""
    from . import messages

    x = conn.execute("select id from exception_record where item_id = %s order by id limit 1", (item["id"],)).fetchone()
    return (messages.text_of(conn, x["id"]) if x else None) or item["title"] or ""


def duplicate_candidate(conn, item_id: int) -> dict | None:
    """The case a case most likely duplicates, for the close-out to suggest (tour F, F9): another case about the same
    equipment (`exceptions.anchors`: a tag, what it is installed at or part of), open, else closed within
    `exceptions.join_hours`, the open and newest first. None when nothing matches."""
    from . import exceptions

    me = conn.execute("select subjects from work_item where id = %s", (item_id,)).fetchone()
    mine = exceptions.anchors(conn, list(me["subjects"] or [])) if me else set()
    if not mine:
        return None
    hours = int(settings.get(conn, "exceptions.join_hours") or 48)
    rows = conn.execute(
        """
        select id, title, subjects from work_item
        where id <> %(i)s and cardinality(subjects) > 0 and state <> 'cancelled' and type <> 'recheck'
          and (closed_at is null or closed_at > now() - make_interval(hours => %(h)s))
          and not exists (select 1 from case_disposition d where d.item_id = work_item.id and d.disposition = 'duplicate')
        order by closed_at is null desc, created_at desc limit 200
        """, {"i": item_id, "h": hours}).fetchall()
    of = exceptions.anchor_map(conn, sorted({s for r in rows for s in r["subjects"]}))  # one query for them all
    return next(({"item_id": r["id"], "title": r["title"]} for r in rows
                 if any(mine & of.get(s, set()) for s in r["subjects"])), None)


FOLLOW_UP = "follow_up_of"  # what a follow-up case carries: the case it follows up
# A follow-up sent to the field is a field job like any other (tour F, F16): an event the field reports back on.
FIELD_FOLLOW_UP = exception_type(
    "follow_up", "Follow-up", audience=("ioc", "field"), joins=False,
    description="What a close-out left to do, sent to a field operator or the operator queue: a field job.").name


def _send_follow_up(conn, item: dict, by: str, to: str, note: str | None = None) -> int:
    """The follow-up a contained close-out planned, as a case for `to`: one per case, linked both ways on the
    history. The close-out's note is what it asks (#1390): it says what is left to do."""
    if have := follow_up(conn, item["id"]):
        return have
    queue = to.startswith("role:")
    from . import exceptions

    if to == f"role:{exceptions.OPERATOR_ROLE}" or (not queue and to in exceptions.operators(conn)):
        return _send_to_field(conn, item, by, to, note)
    # a person's follow-up runs in a queue of theirs with its step handed to them, so they have a step open (#1400)
    lane = to if queue else next((f"role:{r}" for r in work.roles_of(conn, to) if r in work.person_roles(conn)), None)
    subjects = conn.execute("select subjects from work_item where id = %s", (item["id"],)).fetchone()["subjects"]
    case = routing.open_task(conn, "task", f"Follow-up: {item['title']}", by, key=f"follow-up:{item['id']}",
                             role=lane, assignee=None if lane else to,
                             data={FOLLOW_UP: item["id"], **({"summary": note} if note else {})}, subjects=list(subjects or []), source=f"case:{item['id']}")
    if lane and lane != to:
        work.reassign(conn, case["id"], by, to)
    work.comment(conn, item["id"], by, f"Follow-up sent to {to.removeprefix('role:').replace('_', ' ')}"
                 f"{' queue' if queue else ''}: case {case['id']}")
    work.comment(conn, case["id"], by, f"Follows up case {item['id']}, closed with work left to do")
    return case["id"]


def _send_to_field(conn, item: dict, by: str, to: str, note: str | None) -> int:
    """A follow-up for a field operator or the operator queue (tour F, F16): a field job, sent as the IOC sends one, so
    it reaches them as a Field check that asks the close-out's note, never a desk task due in four hours."""
    from . import exceptions

    case = conn.execute("select subjects, priority from work_item where id = %s", (item["id"],)).fetchone()
    e = exceptions.raise_(conn, FIELD_FOLLOW_UP, f"follow-up:{item['id']}", f"Follow-up: {item['title']}", by,
                          refs=list(case["subjects"] or []), priority=case["priority"])
    row = exceptions.dispatch(conn, e["id"], by, to, note or f"Follow up case {item['id']}: {item['title']}")
    conn.execute("update work_item set payload = coalesce(payload, '{}'::jsonb) || %s where id = %s",
                 (Jsonb({FOLLOW_UP: item["id"]}), row["item_id"]))
    work.comment(conn, item["id"], by, f"Follow-up sent to {to.removeprefix('role:').replace('_', ' ')}"
                 f"{' queue' if to.startswith('role:') else ''}: case {row['item_id']}")
    work.comment(conn, row["item_id"], by, f"Follows up case {item['id']}, closed with work left to do")
    return row["item_id"]


def follow_up(conn, item_id: int) -> int | None:
    """The follow-up case sent when this case closed contained, if one was."""
    row = conn.execute(
        "select id from work_item where payload->>%(k)s = %(i)s "
        "union select item_id from work_process where data->>%(k)s = %(i)s and item_id is not null limit 1",
        {"k": FOLLOW_UP, "i": str(item_id)}).fetchone()
    return row["id"] if row else None


def _checklist_said(conn, item_id: int) -> str:
    """The case's checklist as its close-out leaves it: what was done, not needed, or left open."""
    # an item sent as its own case is done when that case is (workflow/checklist.py)
    c = conn.execute(
        "select count(*) filter (where c.state = 'done' or (c.state = 'open' and ch.state = 'done')) as done, "
        "count(*) filter (where c.state = 'skipped') as skipped, "
        "count(*) filter (where c.state = 'open' and ch.state is distinct from 'done') as open, count(*) as total "
        "from work_check c left join work_item ch on ch.id = c.child_item_id where c.item_id = %s", (item_id,)).fetchone()
    if not c["total"]:
        return ""
    parts = [f"{c['done']} of {c['total']} checklist items done"]
    parts += [f"{c['skipped']} not needed"] if c["skipped"] else []
    parts += [f"{c['open']} left open"] if c["open"] else []
    return " (" + ", ".join(parts) + ")"


def offer_feedback(conn, item_id: int, row: dict, by: str) -> list[dict]:
    """Offer the close-out to each detection provider whose alert was on the case and that takes feedback (P3.8c):
    a `detection_feedback` write-back, approved by a person before it is sent. A system that doesn't take it is
    skipped. Correcting the close-out offers the new one."""
    from ..sources import providers
    from . import writeback

    out = []
    for a in providers.feedback_for(conn, item_id):
        payload = {"provider": a["provider"], "alert": a["key"], "case": item_id, "disposition": row["disposition"],
                   "failure_mode": row.get("failure_mode"), "outcome": row.get("outcome"), "note": row.get("note")}
        try:
            out.append(writeback.propose(conn, a["system"], "detection_feedback", payload, by, record=a["key"],
                                         key=f"feedback:{a['system']}:{a['key']}:{item_id}:{row['disposition']}",
                                         title=f"Tell {a['label']} how alert {a['key']} closed: "
                                               f"{DISPOSITIONS[row['disposition']].lower()}"))
        except writeback.WritebackError:
            continue  # its system takes no feedback
    return out


def summary(row: dict, v: dict) -> str:
    """One line for the case's history: "Real: Vibration, cause Failure related to operation / maintenance, Replace."""
    parts = [DISPOSITIONS[row["disposition"]]]
    if row["disposition"] == "real" and (terms := ", ".join(x for x in (
            row["failure_mode"] and v["failure_modes"].get(row["failure_mode"], row["failure_mode"]),
            row["cause"] and f"cause {str(v['causes'].get(row['cause'], row['cause'])).lower()}",
            row["activity"] and str(v["activities"].get(row["activity"], row["activity"])).lower()) if x)):
        parts.append(terms)  # off equipment the failure terms are optional (#1145)
    if row["disposition"] == "duplicate":
        parts.append(f"of case {row['duplicate_of']}")
    if row["outcome"]:
        parts.append(OUTCOMES[row["outcome"]].lower())
    text = ": ".join(parts[:2]) + ("; " + "; ".join(parts[2:]) if len(parts) > 2 else "")
    return text + (f". {row['note']}" if row["note"] else "")


def missing(conn, limit: int = 200) -> list[dict]:
    """Closed cases with no close-out yet, newest first: the ones still to fill in."""
    return conn.execute("select i.id, i.title, i.type, i.closed_at from work_item i "
                        "left join case_disposition d on d.item_id = i.id "
                        "where i.closed_at is not null and i.state = 'done' and d.item_id is null and i.type <> 'recheck' "
                        "order by i.closed_at desc limit %s", (limit,)).fetchall()


RECHECK = "recheck"  # the case type that asks whether a fix held


def reopen_as_repeat(conn, first: int, again: int, by: str, why: str) -> dict:
    """Reopen a closed case as a repeat of itself, linked to the case that showed the problem came back."""
    conn.execute("insert into case_repeat (again, first) values (%s, %s) on conflict do nothing", (again, first))
    state = conn.execute("select state from work_item where id = %s", (first,)).fetchone()["state"]
    if state in ("done", "cancelled"):
        from . import exceptions

        work.close_case(conn, first, "open", by, reason=f"Repeat: {why} (case {again})")
        exceptions.reopen_with_case(conn, first)
    work.comment(conn, first, by, f"Repeat: {why}. See case {again}.")
    return {"first": first, "again": again}


def answer_recheck(conn, item_id: int, by: str, held: bool, note: str | None = None) -> dict:
    """Answer "did the fix hold?" for a closed-out case: kept on its close-out; a no reopens it as a repeat. The
    re-check case closes either way."""
    row = get(conn, item_id)
    if not row or row["disposition"] != "real":
        raise LookupError(f"case {item_id} has no real problem's close-out to re-check")
    note = (note or "").strip() or None
    row = conn.execute("update case_disposition set held = %s, held_note = %s, held_by = %s, held_at = now() "
                       "where item_id = %s returning *", (held, note, by, item_id)).fetchone()
    check = row["recheck_item"]
    if check and conn.execute("select closed_at from work_item where id = %s", (check,)).fetchone()["closed_at"] is None:
        work.close_case(conn, check, "done", by, reason=("The fix held" if held else "The fix did not hold")
                        + (f": {note}" if note else ""))
    if not held:
        reopen_as_repeat(conn, item_id, check or item_id, by, "the fix did not hold" + (f": {note}" if note else ""))
    return row


def _due_rechecks(conn) -> list[int]:
    opened = []
    for d in conn.execute("select d.item_id, d.by, i.title, i.subjects from case_disposition d "
                          "join work_item i on i.id = d.item_id where d.disposition = 'real' and d.recheck_item is null "
                          "and d.held is null and d.recheck_at <= now() and i.state = 'done' "
                          "order by d.recheck_at").fetchall():
        check = routing.open_task(conn, RECHECK, f"Did the fix hold? {d['title']}", by="fuzer",
                                  key=f"recheck:{d['item_id']}", assignee=d["by"], subjects=d["subjects"],
                                  data={"recheck_of": d["item_id"]})["id"]
        conn.execute("update case_disposition set recheck_item = %s where item_id = %s", (check, d["item_id"]))
        opened.append(check)
    return opened


def _repeats(conn, pack: Pack) -> list[dict]:
    """New open cases on the same asset as a real problem closed inside its window, naming the same failure mode."""
    found = []
    rows = conn.execute(
        """
        select n.id as again, n.title, d.item_id as first, d.failure_mode from work_item n
        join case_disposition d on d.disposition = 'real' and d.recheck_at > n.created_at and d.at < n.created_at
        join work_item f on f.id = d.item_id and f.state = 'done' and f.subjects && n.subjects
        where n.closed_at is null and n.type <> %s and n.id <> d.item_id
          and not exists (select 1 from case_repeat r where r.again = n.id)
        order by n.id, d.at desc
        """, (RECHECK,)).fetchall()
    seen: set[int] = set()
    for r in rows:
        if r["again"] in seen:
            continue
        _, text = _text(conn, r["again"])
        if r["failure_mode"] in failure_modes_in(text, pack):
            seen.add(r["again"])
            reopen_as_repeat(conn, r["first"], r["again"], "fuzer",
                             f"{vocabulary(pack)['failure_modes'].get(r['failure_mode'], r['failure_mode'])} again "
                             "inside the re-check window")
            work.cancel_case(conn, r["again"], "fuzer", reason=f"Repeat of case {r['first']}, reopened there")
            found.append({"first": r["first"], "again": r["again"]})
    return found


def sweep(conn, pack: Pack) -> dict:
    """Open the re-checks that are due and fold repeats back into the case they repeat."""
    return {"rechecks": _due_rechecks(conn), "repeats": _repeats(conn, pack)}


def rates(conn) -> dict[tuple[str, str], dict]:
    """How what each work type raised was closed out, by (type, source) (P3.8b, #705): the cases its exceptions were
    on, and how many of those closed as a real problem (confirmed) or a false alarm. A source joined to a case shares
    that case's close-out: a provider's alert on a trip that was real was right."""
    rows = conn.execute(
        """
        select r.type, r.source, count(distinct r.item_id)::int as cases, count(distinct d.item_id)::int as closed,
               count(distinct d.item_id) filter (where d.disposition = 'real')::int as real,
               count(distinct d.item_id) filter (where d.disposition = 'false_alarm')::int as false_alarm
        from exception_record r left join case_disposition d on d.item_id = r.item_id
        where r.item_id is not null group by r.type, r.source
        """).fetchall()
    return {(r["type"], r["source"]): dict(r) for r in rows}


def quality(found: list[dict]) -> dict:
    """Rows of `rates` together: cases, closed out, and the confirmed and false-alarm rates of those closed out."""
    cases, closed = sum(r["cases"] for r in found), sum(r["closed"] for r in found)
    real, false = sum(r["real"] for r in found), sum(r["false_alarm"] for r in found)
    return {"cases": cases, "closed": closed, "real": real, "false_alarm": false,
            "confirmed_rate": real / closed if closed else None, "false_alarm_rate": false / closed if closed else None}

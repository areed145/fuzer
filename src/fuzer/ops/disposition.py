"""Structured close-out (P6.1a, #655): closing a case says what it was and, for a real problem, what failed, why and
what was done, in the ISO 14224 terms of `standards/iso14224.yaml`, so the closed work can be counted and compared.

    real          a real problem: failure mode, cause and action are required; mechanism and outcome are kept
    false_alarm   nothing was wrong
    duplicate     another case already covers it (named by `duplicate_of`)
    data_problem  the data was wrong, not the equipment

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
from ..core.pack import Pack
from ..core.taxonomy import failure_modes_in, load_standard
from . import routing

DISPOSITIONS = {"real": "A real problem", "false_alarm": "False alarm", "duplicate": "Duplicate",
                "data_problem": "Data problem"}
OUTCOMES = {"fixed": "Fixed", "contained": "Contained, follow-up planned", "not_fixed": "Not fixed",
            "monitoring": "Being monitored"}
# words that name the maintenance activity (ISO 14224 Table B.5), first match wins
ACTION_WORDS = (("1", r"\breplac"), ("10", r"\boverhaul"), ("2", r"\brepair|\bfix(ed)?\b"), ("4", r"\badjust|\bre-?set\b"),
                ("7", r"\bservic|\bclean|\bgrease|\blubric"), ("8", r"\btest"), ("9", r"\binspect"),
                ("3", r"\bmodif"), ("6", r"\bcheck"))
FALSE_WORDS = r"\bfalse alarm\b|\bspurious\b|\bnothing (was )?wrong\b|\bno fault\b"
DATA_WORDS = r"\bbad (data|value|reading)\b|\bstale\b|\bwrong unit|\bsensor (fault|drift)\b|\bdata (problem|issue)\b"


def vocabulary(pack: Pack) -> dict:
    """The controlled lists the form picks from: dispositions, outcomes and the taxonomy's failure terms."""
    std = load_standard(pack.classification.get("taxonomy", "iso14224"))
    return {"dispositions": DISPOSITIONS, "outcomes": OUTCOMES, "failure_modes": std.get("failure_modes", {}),
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
    out: dict = {"from": "words"}
    if re.search(FALSE_WORDS, text, re.IGNORECASE):
        out["disposition"] = "false_alarm"
    elif re.search(DATA_WORDS, text, re.IGNORECASE):
        out["disposition"] = "data_problem"
    modes = [m for m in failure_modes_in(text, pack) if m not in ("OTH", "UNK")]
    if modes and "disposition" not in out:
        out |= {"disposition": "real", "failure_mode": modes[0]}
    for code, pat in ACTION_WORDS:
        if re.search(pat, text, re.IGNORECASE):
            out["activity"] = code
            break
    return out


FIELDS = ("disposition", "failure_mode", "mechanism", "cause", "activity", "outcome", "note", "duplicate_of")


def get(conn, item_id: int) -> dict | None:
    return conn.execute("select * from case_disposition where item_id = %s", (item_id,)).fetchone()


def form(conn, pack: Pack, item_id: int) -> dict:
    """Everything the close-out form needs: the record if there is one, the pre-fill and the lists."""
    _text(conn, item_id)
    repeats = conn.execute("select again, at from case_repeat where first = %s order by at", (item_id,)).fetchall()
    return {"item_id": item_id, "record": get(conn, item_id), "suggestion": suggest(conn, pack, item_id),
            "vocabulary": vocabulary(pack), "repeats": repeats}


def record(conn, pack: Pack, item_id: int, by: str, disposition: str, *, failure_mode: str | None = None,
           mechanism: str | None = None, cause: str | None = None, activity: str | None = None,
           outcome: str | None = None, note: str | None = None, duplicate_of: int | None = None,
           suggested: dict | None = None) -> dict:
    """Record the close-out and close the case if it is still open. A real problem needs its failure mode, cause and
    action from the lists; a duplicate names its case. Recording again corrects it."""
    item, _ = _text(conn, item_id)
    v = vocabulary(pack)
    if disposition not in DISPOSITIONS:
        raise ValueError(f"say what it was: {', '.join(DISPOSITIONS)}")
    for name, value, allowed in (("failure mode", failure_mode, v["failure_modes"]), ("mechanism", mechanism, v["mechanisms"]),
                                 ("cause", cause, v["causes"]), ("action", activity, v["activities"]),
                                 ("outcome", outcome, OUTCOMES)):
        if value and str(value) not in {str(k) for k in allowed}:
            raise ValueError(f"{value} is not a {name} on the list")
    if disposition == "real":
        missing = [n for n, x in (("failure mode", failure_mode), ("cause", cause), ("action", activity)) if not x]
        if missing:
            raise ValueError(f"a real problem needs its {', '.join(missing)}")
    else:
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
    if item["closed_at"] is None:
        work.close_case(conn, item_id, "done", by, reason=summary(row, v))
        conn.execute("update work_item set outcome = %s where id = %s and outcome is null", (summary(row, v), item_id))
    else:
        work.comment(conn, item_id, by, f"Close-out: {summary(row, v)}")
    return row


def summary(row: dict, v: dict) -> str:
    """One line for the case's history: "Real: Vibration, cause Failure related to operation / maintenance, Replace."""
    parts = [DISPOSITIONS[row["disposition"]]]
    if row["disposition"] == "real":
        parts.append(", ".join(x for x in (
            v["failure_modes"].get(row["failure_mode"], row["failure_mode"]),
            f"cause {v['causes'].get(row['cause'], row['cause']).lower()}",
            v["activities"].get(row["activity"], row["activity"]).lower()) if x))
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
        work.close_case(conn, first, "open", by, reason=f"Repeat: {why} (case {again})")
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

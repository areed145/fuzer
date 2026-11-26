"""Plan rules (P5.23a, #1420): a rule a planner types in words on Plan, read without a model into one of five typed
rules, kept until it is switched off or removed, and checked against every job still to do.

    deadline   A0130 done by 15 Apr                  each of its jobs finishes by then (the end of that day when no
                                                     time is given)
    duration   no job longer than 36 h               each job (on a resource: no MECH job longer than 36 h) runs no
                                                     longer
    gap        at least 12 h between jobs on MECH    each job on the resource starts at least that long after the one
                                                     before it ends
    overlap    no overlap on crane                   no two jobs on the resource at the same time
    sequence   A0220 before A0130                    every job of the second starts once the last of the first is done

A job is named as Plan names it: a schedule's activity (A0130), an SAP order (its bar and its operations) or a schedule
(OK-TAR-2026, every activity in it). A resource by its code, its kind or a word of its name, as a plan change's
constraints are (replan._resources).

    read(conn, text)              the typed rule, or a PlanError naming the forms
    add / switch / remove         a planner's rules
    rules(conn)                   each rule said back in words, with the jobs breaking it while it is on
    breaks(conn)                  per job, the rules it breaks (Plan's red mark on its bar)
"""

import re
from datetime import timedelta

from psycopg.types.json import Jsonb

from ..core import clock, gates
from ..core.jsonsafe import plain
from ..core.plan import PlanError
from .plan import ACTIVE, _when
from .replan import _day, _resources, _say

FORMS = ("A0130 done by 15 Apr", "no job longer than 36 h", "at least 12 h between jobs on MECH",
         "no overlap on crane", "A0220 before A0130")
_HOURS = r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)"


def _named(conn, ref: str) -> str:
    """A job's name as typed, checked against the plan: an activity, an SAP order or a schedule."""
    r = ref.strip()
    if not conn.execute("select 1 from plan_item where status = any(%(a)s) and (upper(code) = upper(%(r)s) "
                        "or upper(project) = upper(%(r)s) or (source = 'sap' and source_key = %(r)s)) limit 1",
                        {"a": list(ACTIVE), "r": r}).fetchone():
        raise PlanError(f"nothing on the plan is called {r}: name an activity (A0130), an SAP order or a schedule")
    return r


def _on(conn, word: str) -> tuple[list[str], str]:
    found = _resources(conn, word)
    if not found:
        raise PlanError(f"no shared resource is called {word.strip()}")
    return [r["code"] for r in found], " and ".join(r["name"] for r in found)


def read(conn, text: str) -> dict:
    """A rule typed in words, read without a model into one of the five forms, with `say`, the rule said back. Anything
    else is refused, naming the forms. A day with no year is the first such day from a month before the job's start."""
    t = " ".join((text or "").split()).rstrip(".")
    if m := re.fullmatch(r"(\S+) (?:is |are |must be )?(?:done|finished|complete|completed) by (.+)", t, re.IGNORECASE):
        ref = _named(conn, m[1])
        by = _day(m[2], min((j["start_at"] for j in _of(_jobs(conn), ref)), default=None) or clock.aware(clock.now()))
        if ":" not in m[2]:
            by += timedelta(days=1)  # by 15 Apr is by the end of 15 Apr
        return {"text": t, "form": "deadline", "ref": ref, "by": by, "say": f"{ref} done by {_say(by)}"}
    if m := re.fullmatch(rf"no (?:(.+?) )?jobs? (?:longer|more) than {_HOURS}", t, re.IGNORECASE):
        codes, names = _on(conn, m[1]) if m[1] else ([], "")
        hours = float(m[2])
        return {"text": t, "form": "duration", "resources": codes, "hours": hours,
                "say": f"no job{f' on {names}' if names else ''} longer than {hours:g} h"}
    if m := re.fullmatch(rf"(?:at least )?{_HOURS} between (?:jobs |work )?on (.+)", t, re.IGNORECASE):
        codes, names = _on(conn, m[2])
        hours = float(m[1])
        return {"text": t, "form": "gap", "resources": codes, "hours": hours,
                "say": f"at least {hours:g} h between jobs on {names}"}
    if m := re.fullmatch(r"no (?:overlap|overlapping jobs|two jobs at once) on (.+)", t, re.IGNORECASE):
        codes, names = _on(conn, m[1])
        return {"text": t, "form": "overlap", "resources": codes, "say": f"no two jobs on {names} at the same time"}
    if m := re.fullmatch(r"(\S+) (?:before|ahead of|then) (\S+)", t, re.IGNORECASE):
        first, then = _named(conn, m[1]), _named(conn, m[2])
        return {"text": t, "form": "sequence", "first": first, "then": then,
                "say": f"{then} starts once {first} is done"}
    raise PlanError(f"Fuzer reads five forms: {'; '.join(FORMS)}")


# ---- checking ------------------------------------------------------------------------------------------------------

def _jobs(conn) -> list[dict]:
    """Every job still to do, with the resources it asks for."""
    return conn.execute(
        """
        select p.id, p.title, p.code, p.project, p.source, p.source_key, p.parent_id, p.start_at, p.end_at,
               exists (select 1 from plan_item c where c.parent_id = p.id) as summary,
               coalesce((select array_agg(x.resource) from plan_demand_all x where x.item_id = p.id), '{}') as resources
        from plan_item p where p.status = any(%s) order by p.start_at, p.id
        """, (list(ACTIVE),)).fetchall()


def _of(jobs: list[dict], ref: str) -> list[dict]:
    """The jobs a name means: an activity, every activity of a schedule, or an SAP order's bar and its operations."""
    r = ref.upper()
    orders = {j["id"] for j in jobs if j["source"] == "sap" and (j["source_key"] or "").upper() == r}
    return [j for j in jobs if (j["code"] or "").upper() == r or j["id"] in orders or j["parent_id"] in orders
            or ((j["project"] or "").upper() == r and not j["summary"])]


def _hit(j: dict, say: str) -> dict:
    return {"item_id": j["id"], "title": j["title"], "code": j["code"], "start": j["start_at"], "end": j["end_at"],
            "say": say}


def check(rule: dict, jobs: list[dict]) -> list[dict]:
    """The jobs breaking one rule, each with how."""
    form = rule["form"]
    if form == "deadline":
        by = _when(rule["by"])
        return [_hit(j, f"finishes {_say(j['end_at'])}, after {_say(by)}") for j in _of(jobs, rule["ref"])
                if j["end_at"] > by]
    if form == "sequence":
        first = _of(jobs, rule["first"])
        if not first:
            return []
        done = max(j["end_at"] for j in first)
        ids = {j["id"] for j in first}
        return [_hit(j, f"starts {_say(j['start_at'])}, before {rule['first']} is done {_say(done)}")
                for j in _of(jobs, rule["then"]) if j["id"] not in ids and j["start_at"] < done]
    on = [j for j in jobs if not j["summary"] and (not rule.get("resources") or set(j["resources"]) & set(rule["resources"]))]
    if form == "duration":
        out = []
        for j in on:
            hours = (j["end_at"] - j["start_at"]).total_seconds() / 3600
            if hours > rule["hours"] + 1e-9:
                out.append(_hit(j, f"runs {hours:g} h, over {rule['hours']:g} h"))
        return out
    if form == "overlap":
        out = {}
        for n, a in enumerate(on):
            for b in on[n + 1:]:
                if b["start_at"] >= a["end_at"]:
                    break
                for x, y in ((a, b), (b, a)):
                    out.setdefault(x["id"], _hit(x, f"at the same time as “{y['title']}”"))
        return sorted(out.values(), key=lambda h: (h["start"], h["item_id"]))
    if form == "gap":
        out = []
        for b in on:
            before = [a for a in on if a["id"] != b["id"] and a["end_at"] <= b["start_at"]]
            if not before:
                continue
            a = max(before, key=lambda a: a["end_at"])
            hours = (b["start_at"] - a["end_at"]).total_seconds() / 3600
            if hours < rule["hours"] - 1e-9:
                out.append(_hit(b, f"starts {hours:g} h after “{a['title']}” ends"))
        return out
    return []


def rules(conn) -> list[dict]:
    """Each rule as typed and said back, on or off, with the jobs breaking it while it is on."""
    jobs = _jobs(conn)
    return [{"id": r["id"], "text": r["text"], "form": r["rule"]["form"], "say": r["rule"]["say"], "on": r["enabled"],
             "created_by": r["created_by"], "created_at": r["created_at"],
             "breaks": check(r["rule"], jobs) if r["enabled"] else []}
            for r in conn.execute("select * from plan_rule order by id")]


def breaks(conn) -> dict[int, list[dict]]:
    """Per job, each rule it breaks while the rule is on: the rule said back and how the job breaks it."""
    jobs, out = None, {}
    for r in conn.execute("select * from plan_rule where enabled order by id"):
        jobs = jobs if jobs is not None else _jobs(conn)
        for h in check(r["rule"], jobs):
            out.setdefault(h["item_id"], []).append({"rule": r["id"], "rule_say": r["rule"]["say"], "say": h["say"]})
    return out


# ---- a planner's rules ---------------------------------------------------------------------------------------------

def _by(by: str) -> str:
    if gates.machine(by):
        raise PlanError("a plan rule is typed by a named person")
    return by


def add(conn, text: str, by: str) -> list[dict]:
    rule = read(conn, text)
    conn.execute("insert into plan_rule (text, rule, created_by, updated_by) values (%s, %s, %s, %s)",
                 (rule["text"], Jsonb(plain(rule)), _by(by), by))
    return rules(conn)


def switch(conn, rule_id: int, on: bool, by: str) -> list[dict]:
    """Switch a rule off (it stays, said back, and marks nothing) or on again."""
    row = conn.execute("update plan_rule set enabled = %s, updated_by = %s, updated_at = now() where id = %s "
                       "returning id", (bool(on), _by(by), rule_id)).fetchone()
    if row is None:
        raise PlanError(f"no plan rule {rule_id}")
    return rules(conn)


def remove(conn, rule_id: int, by: str) -> list[dict]:
    row = conn.execute("delete from plan_rule where id = %s returning id", (rule_id,)).fetchone() if _by(by) else None
    if row is None:
        raise PlanError(f"no plan rule {rule_id}")
    return rules(conn)

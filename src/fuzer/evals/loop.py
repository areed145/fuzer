"""The loop acceptance eval (P1.7): every type of work on a bundle, driven through the nine-step loop with the same
domain operations the API calls, and a report of the first step that failed for each type.

    arrive    the work comes in through the front door (`raise_exception`), about an asset in the register
    match     its reference resolves to that asset, nothing left unmatched
    route     it goes where the routing table says: its process, its role's queue, or the board to be dispatched
    triage    someone can see it: in a person's or a queue's Inbox, or on the board for the IOC
    dispatch  the IOC sends it to a field operator with what to check
    field     the field job reads as words (no raw ids) on the operator's list, and they report on site, then done
    close     every remaining decision is taken (the playbook below), and the case and the work close
    count     the closed work is on the record a measure reads: closed with a time and an outcome

A type the field never sees (`audience` without "field") skips dispatch and field: the person holding its queue
works it at the desk. Decisions come from `PLAYBOOK`, one entry per process step, so a new process or work type adds
its decisions here. Agent steps get a scripted answer; nothing calls a model.

The eval writes to the database it runs on (cases open and close), so run it on a copy: a test database or
`fuzer eval --loop` against a scratch project.

Today work arrives synthetically, through the front door every source uses; later epics make each type arrive from
its own source or detector and add its type to the run.
"""

import re
from datetime import timedelta

from ..core import work
from ..core.exceptions import raise_exception
from ..core.findings import external
from ..core.jsonsafe import plain

STEPS = ("arrive", "match", "route", "triage", "dispatch", "field", "close", "count")
IOC, FIELD, BY = "loop-ioc", "loop-field", "loop-eval"
FIELD_ROLE = "operator"
MAX_STEPS = 30  # decisions per case before the run gives up: a process that loops forever fails

# The human decisions (and scripted agent answers) per process step. A step not listed is completed with no data.
PLAYBOOK: dict[str, dict[str, dict]] = {
    "upset": {
        "triage": {"triage": "real", "triage_note": "Confirmed by the field check."},
        "diagnose": {"diagnosis": "Scripted diagnosis for the loop eval.", "proposed_action": "Inspect and repair."},
        "approve": {"approval": "approved"},
        "draft_notification": {"notification_text": "Inspect and repair (loop eval)."},
        "raise_notification": {"notification": "LOOP-1"},
        "confirm": {"outcome": "resolved"},
    },
    "iow_breach": {"review": {"iow_decision": "close", "note": "No damage expected (loop eval)."}},
}

# What a type's own source says when it raises one, where its process routes or correlates on it.
ARRIVE: dict[str, dict] = {
    "iow_breach": {"iow": "{key}", "level": "critical"},
    "upset": {"incident_id": "{key}", "ongoing": True},
}

_ID = re.compile(r"\bfz-[\w-]+")  # a Fuzer id; a person reads names, never these


class Step(Exception):
    """A loop step that did not complete, with what was wrong."""


def _people(conn) -> None:
    from ..workflow import bpmn, engine

    engine.set_person(conn, IOC, sorted(bpmn.PERSON_ROLES - {FIELD_ROLE}), by=BY)
    engine.set_person(conn, FIELD, [FIELD_ROLE], by=BY)


def _assets(conn, n: int) -> list[str]:
    """`n` tagged assets no open work is about, so each type's case is its own (none joins another's)."""
    return [f"tag:{r['key']}" for r in conn.execute(
        """
        select k.key from entity_key k join entity e on e.fuzer_id = k.fuzer_id
        where k.kind = 'tag' and not exists (select 1 from exception_record x where x.state = 'open'
                                             and k.fuzer_id = any(x.subjects))
        order by k.key limit %s
        """, (n,))]


def _waiting(conn, item_id: int) -> list[dict]:
    return conn.execute(
        """
        select t.id, t.kind, t.element, t.role, t.assignee, t.due_at, p.process_key
        from work_token t join work_process p on p.id = t.process_id
        where t.item_id = %s and t.state = 'waiting' order by t.id
        """, (item_id,)).fetchall()


def _work_through(conn, item_id: int) -> None:
    """Take every remaining decision on the case from the playbook until nothing waits."""
    from ..workflow import engine

    for _ in range(MAX_STEPS):
        open_steps = _waiting(conn, item_id)
        if not open_steps:
            return
        t = open_steps[0]
        data = PLAYBOOK.get(t["process_key"], {}).get(t["element"], {})
        if t["kind"] in ("user", "fix"):
            engine.complete(conn, t["id"], t["assignee"] or IOC, data)
        elif t["kind"] == "agent":
            engine.complete(conn, t["id"], f"agent:{t['role']}", data)
        elif t["kind"] == "timer":
            engine.tick(conn, now=t["due_at"] + timedelta(minutes=1))
        else:
            raise Step(f"waits on a {t['kind']} at '{t['element']}' that nothing in the loop sends")
    raise Step(f"still open after {MAX_STEPS} decisions: '{_waiting(conn, item_id)[0]['element']}' keeps coming back")


def _row(conn, exception_id: int) -> dict:
    return conn.execute("select * from exception_record where id = %s", (exception_id,)).fetchone()


def _visible(conn, row: dict, route: dict) -> str:
    """Where someone can see the work: a person's or queue's Inbox, or the board."""
    if not row["item_id"]:
        if route["starts"] != "dispatch":
            raise Step(f"no case opened, but its route {route['starts']}s")
        return "on the board, to dispatch"
    mine = work._engine().my_work(conn, IOC)
    ids = {w["item_id"] for w in mine.get("mine", []) + mine.get("queue", [])}
    if row["item_id"] in ids:
        return "in the IOC's Inbox"
    waiting = _waiting(conn, row["item_id"])
    if waiting and waiting[0]["kind"] == "agent":
        return f"with agent:{waiting[0]['role']}"
    raise Step("its case is in nobody's Inbox")


def _routed(conn, row: dict, route: dict) -> str:
    if route["starts"] == "dispatch":
        if row["item_id"]:
            raise Step("opened a case, but its route waits for the IOC to dispatch")
        return "waits on the board"
    if not row["item_id"]:
        raise Step(f"no case, but its route {route['starts']}s")
    procs = [r["process_key"] for r in conn.execute("select process_key from work_process where item_id = %s",
                                                    (row["item_id"],))]
    if route["process"] and route["process"] not in procs:
        raise Step(f"its case runs {procs or 'no process'}, not {route['process']}")
    if route["starts"] == "queue":
        roles = {t["role"] for t in _waiting(conn, row["item_id"])}
        if route["role"] not in roles:
            raise Step(f"its case waits on {sorted(r for r in roles if r) or 'nobody'}, not the {route['role']} queue")
        return f"in the {route['role']} queue"
    return f"started {route['process']}"


def _readable(job: dict) -> None:
    """What the operator reads: a title, assets by name, the finding as labelled values, and no raw ids."""
    said = [job["title"], *(a.get("name") or a.get("label") or "" for a in job["assets"]),
            *(f"{i['label']} {i.get('value')}" for i in job["finding"]["items"])]
    if not job["assets"]:
        raise Step("the field job names no asset to go to")
    if bad := [s for s in said if _ID.search(str(s))]:
        raise Step(f"the field job shows raw ids: {bad[:3]}")


def run_type(conn, route: dict, ref: str, run_id: str = "") -> dict:
    """One type through the loop. Returns {type, starts, steps: {step: what happened}, failed: step or None,
    why: the failure}."""
    from ..ops import exceptions as ex

    t = ex.types(conn)[route["type"]]
    field = "field" in t.audience
    out = {"type": route["type"], "starts": route["starts"], "steps": {}, "failed": None, "why": None}
    row: dict = {}

    def arrive():
        nonlocal row
        key = f"loop:{route['type']}:{run_id}"
        said = {k: v.format(key=key) if isinstance(v, str) else v for k, v in ARRIVE.get(route["type"], {}).items()}
        row = raise_exception(conn, route["type"], key, f"Loop eval: {t.label}", BY, refs=[ref],
                              finding=external(BY, {"seen": "in the loop eval", **said}))
        conn.commit()
        if not row.get("raised"):
            raise Step("an open exception of this type and key was already there")
        return f"raised about {ref}"

    def match():
        if row["unmatched"] or not row["subjects"]:
            raise Step(f"{ref} matched no asset")
        return f"matched {len(row['subjects'])} asset"

    def triage():
        return _visible(conn, _row(conn, row["id"]), route)

    def dispatch():
        if not field:
            return "skipped: the field doesn't see this type"
        ex.dispatch(conn, row["id"], IOC, FIELD, reason="Loop eval: check it on site")
        conn.commit()
        if not [j for j in ex.field_jobs(conn, FIELD) if j["id"] == row["id"]]:
            raise Step("dispatched, but not on the operator's job list")
        return f"sent to {FIELD}"

    def field_leg():
        if not field:
            return "skipped: the field doesn't see this type"
        [job] = [j for j in ex.field_jobs(conn, FIELD) if j["id"] == row["id"]]
        out["job"] = plain(job)  # what the phone was given, for the 390 px check (ui/tests/fixtures)
        _readable(job)
        if job["sent"] is None or not (job["sent"].get("reason") or "").strip():
            raise Step("the field job doesn't say what the IOC asked")
        ex.report(conn, row["id"], FIELD, "on_site")
        ex.report(conn, row["id"], FIELD, "done", note="Checked on site (loop eval).")
        conn.commit()
        return "on site, then done"

    def close():
        r = _row(conn, row["id"])
        if not r["item_id"]:
            raise Step("nothing opened a case to close")
        _work_through(conn, r["item_id"])
        conn.commit()
        case = conn.execute("select state from work_item where id = %s", (r["item_id"],)).fetchone()
        if case["state"] not in ("done", "cancelled"):
            if not field:  # worked at the desk: the queue holder closes it
                work.close_case(conn, r["item_id"], "done", IOC, reason="Worked at the desk (loop eval).")
                conn.commit()
            else:
                raise Step(f"its case is still {case['state']} with nothing waiting")
        ex.reconcile(conn)
        conn.commit()
        if _row(conn, row["id"])["state"] == "open":
            raise Step("the case closed, but the exception is still open")
        return "case and exception closed"

    def count():
        r = _row(conn, row["id"])
        case = conn.execute("select closed_at, outcome, state from work_item where id = %s", (r["item_id"],)).fetchone()
        if not case["closed_at"]:
            raise Step("closed without a closing time, so no measure can count it")
        return f"closed {case['state']}" + (f" ({case['outcome']})" if case["outcome"] else "")

    for name, fn in (("arrive", arrive), ("match", match), ("route", lambda: _routed(conn, _row(conn, row["id"]), route)),
                     ("triage", triage), ("dispatch", dispatch), ("field", field_leg), ("close", close),
                     ("count", count)):
        try:
            out["steps"][name] = fn()
        except Exception as e:  # a step that raised is the step that failed; the rest don't run
            conn.rollback()
            out["failed"], out["why"] = name, str(e) if isinstance(e, Step) else f"{type(e).__name__}: {e}"
            break
    return out


def run(conn, only: list[str] | None = None) -> dict:
    """Every type in the routing table (or `only` these) through the loop on this database."""
    from ..ops import routing
    from ..workflow import engine

    engine.load_defaults(conn)
    _people(conn)
    conn.commit()
    routes = [r for r in routing.routes(conn) if only is None or r["type"] in only]
    refs = _assets(conn, len(routes))
    if len(refs) < len(routes):
        raise ValueError(f"the register has {len(refs)} free tagged assets for {len(routes)} types")
    run_id = conn.execute("select to_char(now(), 'YYYYMMDDHH24MISSUS') as t").fetchone()["t"]  # one key per run
    results = [run_type(conn, r, ref, run_id) for r, ref in zip(routes, refs, strict=True)]
    return {"types": results, "passed": sum(not r["failed"] for r in results), "failed": [
        f"{r['type']}: {r['failed']}: {r['why']}" for r in results if r["failed"]]}

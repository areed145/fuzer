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

Work arrives the way it does in production (P1.7c, Gate A): each bundle's `evals/loop.yaml` says, per type, what
brings it in: a registered detector run as a schedule runs it (`detectors.run`) over the window the bundle names, the
window detectors through the SRE scan and hand-off, a source's sync, or the register build's own questions. The arrive
step runs that and takes the exception it raised. A type the bundle names no arrival for is raised by the loop itself
and reported `synthetic`, so the report shows what is not yet covered. Before the run, the processes load and the
cases opened before they did run them (`routing.adopt_open_cases`), as on the API's start. Work about a source rather
than an asset (`about: source`) matches one of the project's sources instead of an asset (#747).
"""

import re
from datetime import timedelta
from pathlib import Path

import yaml

from ..core import work
from ..core.exceptions import clear_exception, raise_exception
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

# A source's sync that raises its own work, by the name a bundle's evals/loop.yaml uses.
SOURCES = {"sap_notifications": lambda conn: __import__("fuzer.ops.intake", fromlist=["intake"]).sync_notifications(
    conn, enabled=True)}

# A check production runs on a schedule or after each load that raises data work, by the name evals/loop.yaml uses.
SYNCS = {
    "source_health": lambda conn: __import__("fuzer.sources.alerts", fromlist=["alerts"]).check(conn, by="sources"),
    "scorecard": lambda conn: __import__("fuzer.sources.scorecard", fromlist=["scorecard"]).sync(conn),
}

_ID = re.compile(r"\bfz-[\w-]+")  # a Fuzer id; a person reads names, never these


class Step(Exception):
    """A loop step that did not complete, with what was wrong."""


def arrivals(conn) -> dict[str, dict]:
    """How each type arrives on this project's bundle: its evals/loop.yaml `arrive` map, or none."""
    from ..core import bundles
    from ..core.pack import resolve

    folder = bundles.find(resolve(conn).name)  # this database's own pack (not one cached for another database)
    path = Path(folder) / "evals" / "loop.yaml" if folder else None
    from ..sources.relative_dates import resolve_all

    if not (path and path.is_file()):
        return {}
    return resolve_all((yaml.safe_load(path.read_text()) or {}).get("arrive", {}))  # `today-1d 03:10` reads as a date


def _bring(conn, how: dict) -> str:
    """Run what brings a type in, as production runs it. Returns what ran, in words."""
    from ..core import detectors
    from ..core.pack import resolve

    pack, how = resolve(conn), dict(how)
    if "detector" in how:
        name = how.pop("detector")
        out = detectors.run(conn, pack, name, **how)
        if isinstance(out, dict) and out.get("status") in (detectors.OFF, detectors.NOT_APPLICABLE):
            raise Step(f"{name} did not run: {out['note']}")
        return name
    if how.get("scan") == "sre":  # the window detectors raise upsets through the SRE scan and its hand-off
        from ..ops import sre, sre_handoff

        sre.scan(conn, pack, how["since"], how["until"])
        sre_handoff.handoff(conn, pack, max_diagnoses=0)
        return "the SRE scan's window detectors"
    if "source" in how:
        if how["source"] not in SOURCES:
            raise Step(f"no source sync called {how['source']}")
        SOURCES[how["source"]](conn)
        return f"the {how['source']} sync"
    if how.get("build"):
        return "the register build"
    if "sync" in how:
        if how["sync"] not in SYNCS:
            raise Step(f"no sync called {how['sync']}")
        SYNCS[how["sync"]](conn)
        return f"the {how['sync']} check"
    raise Step(f"evals/loop.yaml says neither a detector, a scan, a source nor the build: {how}")


def _arrived(conn, kind: str, since, build: bool, about: str = "asset") -> dict | None:
    """The exception that came in: open, about an asset (or, for work about a source, about one of the project's
    sources), its own case (not joined to another), not the loop's."""
    return conn.execute(
        """
        select * from exception_record where type = %s and state = 'open' and joined_to is null
          and (cardinality(subjects) > 0) = %s and coalesce(source, '') <> %s and (%s or last_seen >= %s)
        order by last_seen desc, id desc limit 1
        """, (kind, about != "source", BY, build, since)).fetchone()


def _source_of(conn, row: dict) -> str | None:
    """The project source or dataset a piece of work about a source names: in its finding, else its key."""
    from ..sources import catalog

    names = set(catalog.datasets(conn)) | {r["source"] for r in conn.execute("select distinct source from crosswalk")}
    names |= {r["name"] for r in conn.execute("select name from source_system")}
    said = [str((e.get("data") or {}).get(k) or "") for e in (row.get("evidence") or []) if isinstance(e, dict)
            for k in ("dataset", "source")]
    for name in [*said, *(row["key"] or "").replace(":", " ").split()]:
        if name in names or name.lower() in names:
            return name
    return None


def _people(conn) -> None:
    from ..workflow import bpmn, engine

    engine.set_person(conn, IOC, sorted(bpmn.PERSON_ROLES - {FIELD_ROLE}), by=BY)
    engine.set_person(conn, FIELD, [FIELD_ROLE], by=BY)


def _assets(conn, n: int) -> list[str]:
    """`n` tagged assets no open work is about, none sharing what it stands for on the plant (`anchors`) with
    another or with open work, so each type's case is its own (none joins another's)."""
    from ..ops.exceptions import anchors

    taken = anchors(conn, [s for r in conn.execute("select subjects from exception_record where state = 'open'")
                           for s in r["subjects"] or []])
    out = []
    for r in conn.execute("select k.key, k.fuzer_id from entity_key k join entity e on e.fuzer_id = k.fuzer_id "
                          "where k.kind = 'tag' order by k.key"):
        mine = anchors(conn, [r["fuzer_id"]])
        if mine & taken:
            continue
        out.append(f"tag:{r['key']}")
        taken |= mine
        if len(out) == n:
            break
    return out


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


def _set_aside(conn, since, kept: int | None) -> int:
    """What an arrival brought in besides the exception the loop worked (the other breaches a scan found), cleared
    with its case, so it doesn't host the next type's work: in production a person works each, here none is ours."""
    rows = conn.execute("select id, type, key, item_id from exception_record where state = 'open' and first_seen >= %s "
                        "and id <> %s", (since, kept or 0)).fetchall()
    for r in rows:
        if r["item_id"] and conn.execute("select 1 from work_item where id = %s and closed_at is null",
                                         (r["item_id"],)).fetchone():
            work.close_case(conn, r["item_id"], "cancelled", BY, reason="Brought in with another (loop eval).")
        clear_exception(conn, r["type"], r["key"], BY, reason="Brought in with another (loop eval).")
    conn.commit()
    return len(rows)


def _names(conn, ids) -> list[str]:
    return [r["label"] for r in conn.execute("select label from entity_label where fuzer_id = any(%s) order by label",
                                             (list(ids),))] or list(ids)


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
            *(f"{i['label']} {i.get('text') if i.get('kind') == 'asset' else i.get('value')}"  # an asset reads by name
              for i in job["finding"]["items"])]
    if not job["assets"]:
        raise Step("the field job names no asset to go to")
    if bad := [s for s in said if _ID.search(str(s))]:
        raise Step(f"the field job shows raw ids: {bad[:3]}")


def run_type(conn, route: dict, ref: str, run_id: str = "", how: dict | None = None) -> dict:
    """One type through the loop. Returns {type, starts, steps: {step: what happened}, failed: step or None,
    why: the failure}."""
    from ..ops import exceptions as ex

    t = ex.types(conn)[route["type"]]
    field = "field" in t.audience
    out = {"type": route["type"], "starts": route["starts"], "steps": {}, "failed": None, "why": None}
    row: dict = {}
    since = conn.execute("select now() as t").fetchone()["t"]

    def arrive():
        nonlocal row
        if how:
            what = _bring(conn, how)
            conn.commit()
            found = _arrived(conn, route["type"], since, bool(how.get("build")), how.get("about", "asset"))
            if not found:
                raise Step(f"{what} raised no {route['type']} of its own about {'a source' if how.get('about') == 'source' else 'an asset'}")
            if how.get("about") == "source":
                row = {**found, "raised": True, "about_source": _source_of(conn, found)}
                out["arrived"] = what
                return f"{what} raised it about the {row['about_source'] or found['key']} source"
            row = {**found, "raised": True}
            out["arrived"] = what
            return f"{what} raised it about {', '.join(_names(conn, found['subjects']))}"
        out["arrived"] = "synthetic"
        key = f"loop:{route['type']}:{run_id}"
        said = {k: v.format(key=key) if isinstance(v, str) else v for k, v in ARRIVE.get(route["type"], {}).items()}
        row = raise_exception(conn, route["type"], key, f"Loop eval: {t.label}", BY, refs=[ref],
                              finding=external(BY, {"seen": "in the loop eval", **said}))
        conn.commit()
        if not row.get("raised"):
            raise Step("an open exception of this type and key was already there")
        return f"synthetic: raised by the loop about {ref}"

    def match():
        if "about_source" in row:  # work about a source names one of the project's sources, not an asset
            if not row["about_source"]:
                raise Step(f"it names no source or dataset of this project ({row['key']})")
            return f"matched the {row['about_source']} source"
        if row["unmatched"] or not row["subjects"]:
            raise Step(f"{', '.join(row['unmatched']) or ref} matched no asset")
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
    if how and not out["failed"]:
        out["set_aside"] = _set_aside(conn, since, row.get("id"))
    return out


def run(conn, only: list[str] | None = None, arrive: dict | None = None) -> dict:
    """Every type in the routing table (or `only` these) through the loop on this database. `arrive` is how each type
    comes in (the bundle's evals/loop.yaml when not given; {} raises every type synthetically)."""
    from ..ops import routing
    from ..workflow import engine

    engine.load_defaults(conn)
    routing.adopt_open_cases(conn, by=BY)  # as the API's start does: work raised before the processes loaded runs them
    _people(conn)
    conn.commit()
    routes = [r for r in routing.routes(conn) if only is None or r["type"] in only]
    refs = _assets(conn, len(routes))
    if len(refs) < len(routes):
        raise ValueError(f"the register has {len(refs)} free tagged assets for {len(routes)} types")
    run_id = conn.execute("select to_char(now(), 'YYYYMMDDHH24MISSUS') as t").fetchone()["t"]  # one key per run
    arrive = arrivals(conn) if arrive is None else arrive
    results = [run_type(conn, r, ref, run_id, arrive.get(r["type"])) for r, ref in zip(routes, refs, strict=True)]
    return {"types": results, "passed": sum(not r["failed"] for r in results),
            "synthetic": [r["type"] for r in results if r.get("arrived") == "synthetic"], "failed": [
        f"{r['type']}: {r['failed']}: {r['why']}" for r in results if r["failed"]]}

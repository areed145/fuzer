"""A new asset in a day (P8.4): the onboarding journey on one page. Each step a new asset goes through, from its seed
bundle to the loop eval, says whether it is ready, blocked or still to do, and each gap says what it prevents and where
it is fixed:

    bundle            the project and the seed bundle it came from: its pack, facility and timezone
    sources           every dataset's last load: held or failed loads, and datasets never loaded
    source packs      the source packs the bundle names, installed or not (their datasets, work types, detectors)
    operating model   the model the bundle follows: applied as it is, and any difference nobody has named
    context           the register's open questions: matches, disagreements, roles, drawings, criticality, gaps
    roster            the roles the bundle staffs, each with someone on it
    loop eval         how to prove it end to end (`fuzer eval --loop`, on a copy: it writes cases)

`journey(conn)` reads only; nothing on the page changes the project. Each step links to the page that owns the fix.
"""

from fastapi import APIRouter, Depends

from ..core import projects
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)

READY, BLOCKED, TODO = "ready", "blocked", "to do"

# The register's open questions, by work type: what each one left open stops, in words
QUESTIONS = {
    "unmatched_record": "two systems' records of one item stay apart, so neither sees the other's history",
    "unmatched_records": "a source's records describe nothing in the register, so its rows reach no asset",
    "conflicting_sources": "an item carries the value of whichever source ranks first, right or not",
    "unresolved_links": "rows name a parent the register doesn't have, so they hang off nothing",
    "measurement_role_to_confirm": "detectors don't read a point until what it measures is confirmed",
    "drawing_disagreement": "a P&ID and the DCS disagree on a setting, so nobody knows which is right",
    "drawing_line_to_confirm": "a line read from a drawing isn't piping until someone confirms it",
    "curve_to_confirm": "methods don't use a datasheet curve until it is confirmed",
    "criticality_not_assessed": "work on an unrated asset can't be ranked by consequence, and gates treat it as unknown",
    "context_gap": "checks on an asset can't run for want of its points, roles, limits or drawing",
    "mapping_drift": "a dataset's load is held until its column change is accepted",
    "mapping_to_approve": "a new export isn't loaded until its suggested mapping is approved",
    "failed_quality_check": "a dataset's load is held until its export is fixed",
}


def _step(key: str, title: str, status: str, summary: str, gaps: list[dict] | None = None, link: str | None = None,
          **extra) -> dict:
    return {"key": key, "title": title, "status": status, "summary": summary, "gaps": gaps or [], "link": link,
            **extra}


def _gap(what: str, prevents: str, link: str | None = None) -> dict:
    return {"what": what, "prevents": prevents, "link": link}


def _bundle(conn, name: str | None) -> tuple[dict, dict]:
    cfg = projects.config(conn, name) if name else {}
    b = cfg.get("bundle") or {}
    if not name:
        return _step("bundle", "Bundle", BLOCKED, "No project: initialise one from a seed bundle.", [
            _gap("no project in this database", "everything else on this page",
                 None)], command="fuzer init --bundle <folder>"), b
    if not b:
        return _step("bundle", "Bundle", READY, f"Project {name}, configured by hand rather than from a seed bundle.",
                     project=name), b
    from ..core.pack import current_pack

    pack = current_pack(conn)
    return _step("bundle", "Bundle", READY,
                 f"Project {name} from the {b.get('name')} bundle: facility {pack.facility}, "
                 f"timezone {cfg.get('timezone') or 'not set'}.", project=name, bundle=b.get("name"),
                 facility=pack.facility, digest=b.get("digest")), b


def _sources(conn) -> dict:
    rows = conn.execute(
        """
        select d.name, d.status as dataset_status, r.status, r.error from source_dataset d
        left join lateral (select status, error from source_run where dataset_id = d.id order by id desc limit 1) r
          on true
        where d.status = 'active' order by d.name
        """).fetchall()
    if not rows:
        return _step("sources", "Sources", TODO, "No datasets yet: connect a source or install a source pack.",
                     [_gap("no datasets", "the register, detectors and work have nothing to read", "#/sources")],
                     link="#/sources")
    held = [r["name"] for r in rows if r["status"] in ("held", "failed")]
    never = [r["name"] for r in rows if r["status"] is None]
    done = len(rows) - len(held) - len(never)
    gaps = [_gap(f"{d} is {'held' if r['status'] == 'held' else 'failing'}",
                 f"what {d} carries is missing from the register and from the detectors that read it",
                 f"#/sources?dataset={d}") for r in rows if (d := r["name"]) in held]
    if not done:
        gaps.append(_gap("no dataset has loaded", "the register, detectors and work have nothing to read", "#/sources"))
    # A dataset never loaded is usually a built-in one this asset doesn't export (a pipeline has no well tests): listed,
    # not counted as a gap
    return _step("sources", "Sources", BLOCKED if held or not done else READY,
                 f"{done} datasets loaded" + (f", {len(held)} held or failing" if held else "")
                 + (f"; {len(never)} others have never loaded (not exported here, or not connected yet)" if never else "")
                 + ".", gaps, link="#/sources", loaded=done, held=len(held), never_loaded=never)


def _source_packs(conn, b: dict) -> dict:
    listed = b.get("source_packs") or []
    if not listed:
        return _step("source_packs", "Source packs", READY, "The bundle names no source packs beyond the built-in "
                     "sources.", link="#/sources?view=systems")
    have = {r["name"] for r in conn.execute(
        "select s.name from source_system s where exists (select 1 from source_dataset d where d.system_id = s.id)")}
    missing = [p["system"] for p in listed if p["system"] not in have]
    gaps = [_gap(f"{s} isn't installed", f"{s}'s datasets, the work it raises and its detectors aren't on the project",
                 "#/sources?view=systems") for s in missing]
    return _step("source_packs", "Source packs", BLOCKED if missing else READY,
                 f"{len(listed) - len(missing)} of {len(listed)} source packs the bundle names are installed.", gaps,
                 link="#/sources?view=systems")


def _operating_model(conn, b: dict) -> dict:
    from ..workflow import operating_model as om

    ref = b.get("operating_model")
    if not ref:
        return _step("operating_model", "Operating model", TODO, "The bundle names no operating model; the project "
                     "runs the roles, processes and settings it has.", [
                         _gap("no operating model named", "nothing says how this asset should run, so nothing can "
                              "say where it differs", None)])
    try:
        c = om.conformance(conn)
    except om.OperatingModelError as e:
        return _step("operating_model", "Operating model", BLOCKED, str(e), [
            _gap(f"{ref['name']} v{ref['version']} isn't held here", "the project can't adopt the model its "
                 "bundle names", None)])
    gaps = []
    if not c["applied"]:
        gaps.append(_gap(f"{c['model']} v{c['version']} isn't applied", "the project runs whatever roles, SLAs and "
                         "settings it has, not the model its bundle names", None))
    gaps += [_gap(f"{d['part']} {d['key']} is {d['change']} and nobody has named why",
                  "a local difference nobody owns: it can't be told from a mistake", None) for d in c["unnamed"]]
    return _step("operating_model", "Operating model", READY if not gaps else TODO,
                 f"{c['model']} v{c['version']}, " + ("applied" if c["applied"] else "not applied yet")
                 + f"; {c['named']} named deviation{'s' if c['named'] != 1 else ''}, {len(c['unnamed'])} unnamed.",
                 gaps, command="fuzer operating-model adopt", model=c["model"], version=c["version"],
                 applied=c["applied"], named=c["named"], unnamed=len(c["unnamed"]))


def _context(conn) -> dict:
    rows = conn.execute(
        "select type, count(*) as n from exception_record where state = 'open' and type = any(%s) group by type "
        "order by count(*) desc, type", (list(QUESTIONS),)).fetchall()
    if not rows:
        return _step("context", "Context", READY, "No open questions about the register.", link="#/masterdata")
    total = sum(r["n"] for r in rows)
    held = {"mapping_drift", "failed_quality_check"}
    gaps = [_gap(f"{r['n']} {r['type'].replace('_', ' ')}", QUESTIONS[r["type"]],
                 "#/masterdata?tab=questions") for r in rows]
    return _step("context", "Context", BLOCKED if any(r["type"] in held for r in rows) else TODO,
                 f"{total} open question{'s' if total != 1 else ''} about the register, in the data steward's queue.",
                 gaps, link="#/masterdata?tab=questions", questions=total)


def _roster(conn, b: dict) -> dict:
    from ..workflow import engine

    roster = b.get("roster") or []
    if not roster:
        return _step("roster", "Roster", TODO, "The bundle names no roster: say which roles the asset staffs.",
                     link="#/users?view=workflow")
    people = engine.people(conn)
    gaps, filled = [], 0
    for r in roster:
        on = sum(1 for p in people if r["role"] in (p["roles"] or []))
        filled += min(on, r["seats"])
        if not on:
            gaps.append(_gap(f"nobody holds {r['role']}", f"work for {r['role']} waits in its queue with no one to "
                             "take it, and escalates", "#/users?view=workflow"))
        elif on < r["seats"]:
            gaps.append(_gap(f"{r['role']} has {on} of {r['seats']} seats filled",
                             "its queue has fewer people to share it than the roster plans", "#/users?view=workflow"))
    seats = sum(r["seats"] for r in roster)
    return _step("roster", "Roster", BLOCKED if any(g["what"].startswith("nobody") for g in gaps) else
                 TODO if gaps else READY, f"{filled} of {seats} seats filled across {len(roster)} roles.", gaps,
                 link="#/users?view=workflow", seats=seats, filled=filled)


def _loop() -> dict:
    return _step("loop", "Loop eval", TODO, "Every type of work from arrival to counted, as production brings it in. "
                 "It writes cases, so run it on a copy of the project, not here.", [], command="fuzer eval --loop")


def journey(conn) -> dict:
    """The onboarding journey for this project: {project, steps: [{key, title, status, summary, gaps: [{what,
    prevents, link}], link, ...}], ready, blocked, to_do}."""
    name = projects.current(conn)
    first, b = _bundle(conn, name)
    steps = [first]
    if name:
        steps += [_sources(conn), _source_packs(conn, b), _operating_model(conn, b), _context(conn), _roster(conn, b),
                  _loop()]
    count = {s: sum(1 for x in steps if x["status"] == s) for s in (READY, BLOCKED, TODO)}
    return {"project": name, "steps": steps, "ready": count[READY], "blocked": count[BLOCKED], "to_do": count[TODO]}


@router.get("/onboarding")
def onboarding(conn=Depends(get_conn)):
    """The onboarding journey (P8.4): each step a new asset goes through, ready, blocked or to do, and what each gap
    prevents. Reads only."""
    return journey(conn)

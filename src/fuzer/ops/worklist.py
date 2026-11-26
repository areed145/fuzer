"""All the work as one list (#1208 review): every raised event and every case, in one row shape, filtered and sorted
on any of its dimensions. The List and the Board read it; a preset (My work, To dispatch, Data questions) is only a
set of these filters.

    rows(conn, **filters)    {rows, total, facets, labels}: the matching rows (sorted, at most `limit`), how many
                             match, for each dimension how many match on each of its values with that dimension left
                             out, and the names of the facilities, areas and assets the facets count
    figures(conn, person)    the headline counts every page shows (open, overdue, each stage, what waits on the
                             person), counted over these same rows, so a number always equals the List it opens
    dues(conn, ids)          each case's due time and whether it is overdue, the one definition every count uses

An event (an exception) is one row with the case working it, if any. A case no event raised (a task, a handover, a
close-out) is a row of its own. Every row says where it stands (`stage`: dispatch, planned, field, back, desk or
closed, decided once in ops.exceptions.stage), who holds it, when it is due and whether that has passed.

Filters, each optional, a comma list where it says so:
    state       open (the default), closed or all
    kind        the type of work (comma list)
    stage       comma list of the stages above
    holder      a person, `role:<name>` for a queue, or `none` for nobody (comma list)
    queue       the team whose work it is, by its type's role (data_steward, engineer...; `none`) (comma list)
    waiting_on  a person: what waits on them, a step of theirs or one in their roles' queues
    overdue     true for only what is past its due time
    subject     an entity the work is about
    at          places on Home's map (comma list; `unplaced` for none)
    facility    the facility it is at, by its id (comma list; `none`)
    area        the area it is in, by its id (comma list; `none`)
    asset       the well or pipeline it is about, else the tag (the position an instrument or equipment sits at:
                the outermost tag it is part of or installed at), by its id (comma list; `none`)
    within      entities: only work about one of them or about something in one of them (comma list)
    source      what raised it (comma list)
    q           words in the title or key
    person      who is looking: each row's `take` is a step of theirs to take from a queue, if any
    sort        value (the default: what it is worth, then priority, then newest), due, opened, updated or title
"""

from datetime import UTC, datetime

from ..core import work
from ..graph.hierarchy import WORK_KINDS
from . import exceptions

SORTS = ("value", "due", "opened", "updated", "title")
STATES = ("open", "closed", "all")
DIMENSIONS = ("kind", "stage", "holder", "queue", "source", "facility", "area", "asset")
ASSET_KINDS = tuple(k for k in WORK_KINDS if k not in ("tag", "equipment"))  # filed under before a tag or equipment


def _asset(chain: list[dict]) -> dict | None:
    """What a subject's work is filed under as its asset (#1358): the nearest well or pipeline, else the tag it sits
    at, by the register tag people know (equipment is labelled by serial number, so a pump reads as its tag), else
    untagged equipment."""
    return (next((e for e in chain if e["kind"] in ASSET_KINDS), None)
            or next((e for e in reversed(chain) if e["kind"] == "tag"), None)
            or next((e for e in chain if e["kind"] == "equipment"), None))


def _list(v) -> list[str]:
    if v is None or v == "":
        return []
    return [x for x in (v if isinstance(v, (list, tuple)) else str(v).split(",")) if x]


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def dues(conn, ids: list[int]) -> dict[int, dict]:
    """The one definition of when a case is due and whether that has passed (#1352): its earliest waiting person
    step's due time, else its own; overdue once that is before now and the case is open. The List, the counts every
    page shows and Home's map all read it."""
    now = datetime.now(UTC)
    return {i: {"due": c["due_at"], "overdue": bool(c["due_at"] and c["due_at"] < now and c["closed_at"] is None)}
            for i, c in _cases(conn, ids, "all").items()}


def _cases(conn, ids: list[int] | None, state: str) -> dict[int, dict]:
    """Each case's due time (its earliest waiting person step, else its own), the steps waiting and who holds them."""
    where = "i.id = any(%(ids)s)" if ids is not None else (
        "i.closed_at is null" if state == "open" else "i.closed_at is not null" if state == "closed" else "true")
    rows = conn.execute(
        f"""
        select i.id, i.type, i.title, i.state, i.subjects, i.priority, i.assignee, i.role, i.due, i.source,
               i.created_by, i.created_at, i.updated_at, i.closed_at,
               coalesce((select min(t.due_at) from work_token t where t.item_id = i.id and t.state = 'waiting'
                         and t.kind in ('user', 'fix')), i.due) as due_at,
               coalesce((select json_agg(json_build_object('id', t.id, 'assignee', t.assignee, 'role', t.role) order by t.id)
                         from work_token t where t.item_id = i.id and t.state = 'waiting'
                         and t.kind in ('user', 'fix')), '[]') as waiting
        from work_item i where {where}
        """, {"ids": ids}).fetchall()
    return {r["id"]: dict(r) for r in rows}


def _holder(assignee: str | None, role: str | None) -> str:
    return assignee or (f"role:{role.removeprefix('role:')}" if role else "none")


def _all(conn, state: str) -> list[dict]:
    """Every row in the state, unfiltered."""
    exceptions_state = None if state == "all" else ("open" if state == "open" else None)
    events = exceptions.listing(conn, state=exceptions_state, limit=100000)
    if state == "closed":
        events = [e for e in events if e["state"] != "open"]
    item_ids = [e["item_id"] for e in events if e.get("item_id")]
    cases = _cases(conn, item_ids, state)
    kinds = exceptions.types(conn)
    labels = exceptions.work_labels(conn)

    def team(kind: str, role: str | None) -> str:
        t = kinds.get(kind)
        return ((t.role if t and t.role else None) or role or "none").removeprefix("role:")

    now = datetime.now(UTC)
    out = []
    for e in events:
        c = cases.get(e["item_id"]) if e.get("item_id") else None
        due = c["due_at"] if c else None
        out.append({**e, "uid": f"e{e['id']}", "exception_id": e["id"], "kind": e["type"],
                    "kind_label": exceptions.work_label(labels, e["type"]),
                    "state": "open" if e["state"] == "open" else "closed",
                    "exception_state": e["state"], "holder": _holder(e.get("assignee"), e.get("role")),
                    "queue": team(e["type"], e.get("role")),
                    "due_at": _iso(due), "overdue": bool(due and due < now and e["state"] == "open"),
                    "waiting": (c or {}).get("waiting") or [],
                    "opened": _iso(e["first_seen"]), "updated": _iso(e["last_seen"])})
    held = {e["item_id"] for e in events if e.get("item_id")}
    held |= {r["item_id"] for r in conn.execute("select item_id from exception_record where item_id is not null")}
    field_people = exceptions.operators(conn)
    # A case no event raised carries the value its scan stored (an opportunity's worth), the figure its case page shows,
    # so Most valuable doesn't sink a $5.6 M proposal under rows worth nothing (red team #6).
    worth = {r["item_id"]: r["value_usd"] for r in conn.execute(
        "select item_id, max(value_usd) as value_usd from opportunity where item_id is not null and value_usd is not null"
        " group by item_id")}
    for c in _cases(conn, None, state).values():
        if c["id"] in held:
            continue
        closed = c["closed_at"] is not None
        stage = "closed" if closed else ("field" if c["assignee"] in field_people else "desk")
        due = c["due_at"]
        out.append({
            "uid": f"c{c['id']}", "id": None, "exception_id": None, "item_id": c["id"], "type": c["type"],
            "kind": c["type"], "kind_label": exceptions.work_label(labels, c["type"]),
            "key": None, "title": c["title"], "source": c["source"] or c["created_by"],
            "state": "closed" if closed else "open", "exception_state": None, "case_state": c["state"],
            "stage": stage, "priority": c["priority"], "value_usd": worth.get(c["id"]), "subjects": c["subjects"] or [],
            "unmatched": [], "assignee": c["assignee"], "role": c["role"], "holder": _holder(c["assignee"], c["role"]),
            "queue": team(c["type"], c["role"]), "due_at": _iso(due), "overdue": bool(due and due < now and not closed), "waiting": c["waiting"] or [],
            "opened": _iso(c["created_at"]), "updated": _iso(c["updated_at"]), "first_seen": _iso(c["created_at"]),
            "last_seen": _iso(c["updated_at"]), "raised_count": 1, "reports": 1, "joined": [], "evidence": {},
            "planned": None, "back_from_field": False, "shelved_until": None,
        })
    return out


def _shared(conn, state: str) -> tuple[list[dict], dict[str, str]]:
    """Every row in the state (`_all`) with its facility, area and asset (`_place`), and their names.
    One computation serves the reads asked for together and after, until anything is written (core.shared_reads):
    a page asks for the counts, the List and an entity's tabs at once, and each built every row on the 1-CPU server.
    Each caller gets its own copy of every row, so it may set and drop a row's keys."""
    from ..core.shared_reads import shared

    def read():
        rows = _all(conn, state)
        return rows, _place(conn, rows)

    rows, labels = shared(conn, "worklist", (state,), read)
    return [dict(r) for r in rows], dict(labels)


def _place(conn, rows: list[dict]) -> dict[str, str]:
    """Each row's facility, area and asset (the nearest of each kind among its subjects and what they sit in, else
    `none`) and the set of everything it is about or in (`_within`); returns the names of what it found."""
    from . import attention
    up = attention.lineage(conn, sorted({s for r in rows for s in r.get("subjects") or []}))
    labels: dict[str, str] = {}
    for r in rows:
        chains = [up.get(s, []) for s in r.get("subjects") or []]
        r["_within"] = {e["id"] for c in chains for e in c}
        for dim, kinds in (("facility", ("facility",)), ("area", ("area",)), ("asset", None)):
            found = (next((a for c in chains if (a := _asset(c))), None) if kinds is None
                     else next((e for c in chains for e in c if e["kind"] in kinds), None))
            r[dim] = found["id"] if found else "none"
            if found:
                labels[found["id"]] = found["label"]
    return labels


def _holds(r: dict, person: str, acting: list[str] | None = None) -> bool:
    """Whether one of the row's open steps (or the case, when no step is open) is held by the person, in a role they
    act as (as `_waits_on` counts it)."""
    steps = r["waiting"] or ([{"assignee": r.get("assignee"), "role": r.get("role")}] if r.get("item_id") else [])
    return any(s.get("assignee") == person and (acting is None or not (role := (s.get("role") or "").removeprefix("role:"))
                                                or role in acting) for s in steps)


def _waits_on(r: dict, person: str, roles: set[str], acting: list[str] | None = None) -> bool:
    """A step of theirs (in a role they act as, when the demo account picked some: work.acting_roles), or one waiting
    unclaimed in their roles' queues."""
    steps = r["waiting"] or ([{"assignee": r.get("assignee"), "role": r.get("role")}] if r.get("item_id") else [])
    for s in steps:
        role = (s.get("role") or "").removeprefix("role:")
        if s.get("assignee") == person and (acting is None or not role or role in acting):
            return True
        if not s.get("assignee") and role in roles:
            return True
    return "ops_lead" in roles and r["stage"] == "dispatch"  # the ops lead sends what nothing routed


def _value_key(r: dict):
    return (-(r.get("value_usd") or 0), -(r.get("priority") or 0), _neg_time(r.get("updated")))


def _neg_time(v) -> float:
    try:
        return -datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return 0.0


SORT_KEYS = {
    "value": _value_key,
    "due": lambda r: (r.get("due_at") is None, str(r.get("due_at") or ""), _value_key(r)),
    "opened": lambda r: _neg_time(r.get("opened")),
    "updated": lambda r: _neg_time(r.get("updated")),
    "title": lambda r: (r.get("title") or "").lower(),
}


OPEN_STAGES = ("dispatch", "desk", "planned", "field", "back")


def figures(conn, person: str | None = None) -> dict:
    """The one count of how much work there is (#4 of the red team tour). Every figure is a filter of the List over the
    same open rows: `open` is the List with no filter, `overdue` its Overdue toggle, `stages` its Stage picker (and the
    Board's columns), `waiting_on_you` its Waiting on me preset. The badge, Home, My work, the handover and the List
    all read these, so they can't disagree. A report's list of cases is these rows `within` its scope, and says the
    scope beside its count."""
    everything, _ = _shared(conn, "open")
    stages = dict.fromkeys(OPEN_STAGES, 0)
    for r in everything:
        stages[r["stage"]] = stages.get(r["stage"], 0) + 1
    out = {"open": len(everything), "overdue": sum(1 for r in everything if r["overdue"]), "stages": stages,
           "waiting_on_you": None}
    if person:
        roles, acting = set(work.roles_of(conn, person)), work.acting_roles(conn, person)
        mine = [r for r in everything if _waits_on(r, person, roles, acting)]
        # an ops lead's includes what waits to be dispatched; Needs you and My work list that part on its own, and
        # `my_work` is the rest, so Home's My work and the My work page read one number (#1352)
        to_dispatch = sum(1 for r in mine if r["stage"] == "dispatch")
        # My work's chips split the rest (P5.28d): held by them, or waiting unclaimed in their roles' queues, so its
        # All, Assigned to you and Unclaimed add up and Take it moves one from the second to the first
        assigned = sum(1 for r in mine if r["stage"] != "dispatch" and _holds(r, person, acting))
        out["waiting_on_you"] = {"total": len(mine), "overdue": sum(1 for r in mine if r["overdue"]),
                                 "to_dispatch": to_dispatch, "my_work": len(mine) - to_dispatch,
                                 "assigned": assigned, "unclaimed": len(mine) - to_dispatch - assigned}
    return out


def rows(conn, state: str = "open", kind=None, stage=None, holder=None, queue=None, waiting_on: str | None = None,
         overdue: bool | None = None, subject: str | None = None, at=None, source=None, facility=None, area=None,
         asset=None, within=None, q: str | None = None,
         sort: str = "value", limit: int = 500, person: str | None = None) -> dict:
    if state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    everything, every_label = _shared(conn, state)
    if at:
        from . import attention
        everything = attention.only_at(conn, everything, ",".join(_list(at)))
        kept = {e for r in everything for e in (r["facility"], r["area"], r["asset"])}
        labels = {k: v for k, v in every_label.items() if k in kept}
    else:
        labels = every_label
    chosen = {"kind": set(_list(kind)), "stage": set(_list(stage)), "holder": set(_list(holder)),
              "queue": set(_list(queue)), "source": set(_list(source)), "facility": set(_list(facility)),
              "area": set(_list(area)), "asset": set(_list(asset))}
    inside = set(_list(within))
    roles = set(work.roles_of(conn, waiting_on)) if waiting_on else set()
    acting = work.acting_roles(conn, waiting_on) if waiting_on else None
    words = "".join(ch for ch in (q or "").lower() if ch.isalnum())

    def fixed(r: dict) -> bool:  # every filter that is not a dimension with facets
        if waiting_on and not _waits_on(r, waiting_on, roles, acting):
            return False
        if overdue and not r["overdue"]:
            return False
        if subject and subject not in (r.get("subjects") or []):
            return False
        if inside and not inside & r["_within"]:
            return False
        said = "".join(ch for ch in f"{r.get('title') or ''} {r.get('key') or ''}".lower() if ch.isalnum())
        return not words or words in said

    def on(r: dict, dim: str) -> bool:
        return not chosen[dim] or r[dim] in chosen[dim]

    base = [r for r in everything if fixed(r)]
    facets = {}
    for dim in DIMENSIONS:
        counted: dict[str, int] = {}
        for r in base:
            if all(on(r, d) for d in DIMENSIONS if d != dim):
                counted[r[dim]] = counted.get(r[dim], 0) + 1
        facets[dim] = dict(sorted(counted.items(), key=lambda kv: -kv[1]))
    matched = [r for r in base if all(on(r, d) for d in DIMENSIONS)]
    facets["overdue"] = sum(1 for r in matched if r["overdue"])
    matched.sort(key=SORT_KEYS[sort])
    # `take`: the step waiting unclaimed in a queue of a role the looker acts in, so the row offers Take it (My work
    # folded into the List); the same roles Waiting on me counts by.
    mine = (roles if person == waiting_on else set(work.roles_of(conn, person))) if person else set()
    for r in matched:
        steps = r.pop("waiting", None) or []
        r["take"] = next((s["id"] for s in steps if s.get("id") and not s.get("assignee")
                          and (s.get("role") or "").removeprefix("role:") in mine), None) if r["state"] == "open" else None
        r.pop("_within", None)
    labels.update({e: e for e in inside if e not in labels})
    return {"rows": matched[:limit], "total": len(matched), "facets": facets, "labels": labels}

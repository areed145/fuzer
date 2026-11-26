"""Simultaneous operations (P5.11b, #709): two jobs at the same time that must not run side by side, said in plain words
before either is sent. Read from the records, never from equipment type:

    same asset   two jobs on the same equipment at once
    hazard       two jobs in the same area whose permits to work clash: hot work beside a line break or a confined
                 space entry, or anything under a lift (the permit's type names the hazard)
    bypass       two jobs on the same system while one of its safeguards is bypassed (P3.9): the system is the
                 equipment, its train and what drives it, from the piping (safeguards.reach), never from tag names

A job is an open exception sent to the field or a plan item, with its subjects, its area and its time. The check runs
in readiness at dispatch (a conflict blocks until a person says why it goes anyway) and on Plan, beside the asset and
crew clashes, so the planner and the ops lead see the same words.

A bypass counts while it is in place now; a plan item further out is checked against today's bypasses, which is the
cautious reading (a bypass due out before the job may still be in).
"""

from datetime import UTC, datetime, timedelta

from ..graph import structure

# A permit type, as sites write it, to the hazard it brings.
HAZARD_WORDS = {
    "hot": ("hot work", "welding", "grinding", "naked flame"),
    "break": ("line break", "breaking containment", "opening", "vessel opening"),
    "entry": ("confined space", "vessel entry"),
    "lift": ("lift", "crane"),
}
# Hazards that must not share an area at the same time, and how to say it.
CLASHES = {
    frozenset({"hot", "break"}): "hot work near a line break",
    frozenset({"hot", "entry"}): "hot work beside a confined space entry",
}
LIVE_PERMIT = ("requested", "approved", "issued", "live", "active", "suspended")
SOON = timedelta(hours=12)  # at dispatch: plan items starting within this window count as at the same time


def hazard(permit_type: str | None) -> str | None:
    t = (permit_type or "").lower()
    return next((h for h, words in HAZARD_WORDS.items() if any(w in t for w in words)), None)


def _hazards(conn, subjects: list[str]) -> list[dict]:
    """The hazards of the permits (not closed or expired) covering a job's subjects and where they are installed."""
    return _hazards_of(conn, {0: subjects})[0]


def _hazards_of(conn, jobs: dict) -> dict:
    """`_hazards` for many jobs ({key: subjects}) in two reads, not two per job. A job's places are its subjects,
    what is installed at them and where they are installed (as readiness reads them)."""
    every = sorted({s for subjects in jobs.values() for s in subjects or []})
    if not every:
        return {k: [] for k in jobs}
    near: dict[str, set[str]] = {s: {s} for s in every}
    for r in conn.execute(
            "select dst as s, src as id from edge where rel = 'installed_at' and dst = any(%(s)s) and valid_to is null "
            "union select src as s, dst as id from edge where rel = 'installed_at' and src = any(%(s)s) "
            "and valid_to is null", {"s": every}).fetchall():
        near[r["s"]].add(r["id"])
    places = sorted({p for ps in near.values() for p in ps})
    covering: dict[str, list[dict]] = {}
    for r in conn.execute(
        """
        select c.dst as place, pl.label as permit, gt.value #>> '{}' as type, gs.value #>> '{}' as state
        from edge c join entity p on p.fuzer_id = c.src and p.kind = 'permit'
        left join entity_label pl on pl.fuzer_id = p.fuzer_id
        left join golden gt on gt.fuzer_id = p.fuzer_id and gt.field = 'permit_type'
        left join golden gs on gs.fuzer_id = p.fuzer_id and gs.field = 'state'
        where c.rel = 'covers' and c.dst = any(%s)
        order by pl.label
        """, (places,)).fetchall():
        covering.setdefault(r["place"], []).append(r)
    out = {}
    for k, subjects in jobs.items():
        mine = {p for s in subjects or [] for p in near.get(s, ())}
        rows = sorted((r for p in mine for r in covering.get(p, [])), key=lambda r: (r["permit"] is None, r["permit"] or ""))
        out[k] = [{"hazard": h, "permit": r["permit"], "type": r["type"]} for r in rows
                  if (r["state"] or "issued").lower() in LIVE_PERMIT and (h := hazard(r["type"]))]
    return out


def _label(conn, ids: list[str]) -> str:
    if not ids:
        return "it"
    row = conn.execute("select label from entity_label where fuzer_id = %s", (ids[0],)).fetchone()
    return row["label"] if row and row["label"] else ids[0]


def job(conn, title: str, subjects: list[str], area: str | None = None, ref: dict | None = None,
        plant=None, on_file: list[dict] | None = None, hazards: list[dict] | None = None) -> dict:
    """A job as the check sees it: its title, subjects, area and the hazards its permits bring. `plant` is
    structure.load and `on_file` the bypasses on file (safeguards.records), read once by a caller making several."""
    from .plan import _area
    from .safeguards import bypasses

    subjects = list(subjects or [])
    return {"title": title, "subjects": subjects, "area": area if area is not None else _area(conn, subjects),
            "hazards": _hazards(conn, subjects) if hazards is None else hazards, "ref": ref or {},
            "bypasses": bypasses(conn, subjects, system=True, plant=plant, on_file=on_file) if subjects else []}


def conflicts(conn, a: dict, b: dict, *, same_asset: bool = True) -> list[dict]:
    """What makes two jobs at the same time unsafe side by side, each in plain words."""
    out = []
    shared = [s for s in a["subjects"] if s in b["subjects"]]
    if same_asset and shared:
        out.append({"kind": "same_asset", "say": f"“{b['title']}” is on {_label(conn, shared)} at the same time"})
    theirs = {x["id"] for x in b.get("bypasses") or []}
    for x in a.get("bypasses") or []:
        if x["id"] in theirs:
            out.append({"kind": "bypass", "bypass": x["label"], "say": (
                f"“{b['title']}” is on the same system, where a safeguard is bypassed: {x['say']}")})
    if a["area"] and a["area"] == b["area"] and a["hazards"] and b["hazards"]:
        area = _label(conn, [a["area"]])
        for x in a["hazards"]:
            for y in b["hazards"]:
                say = CLASHES.get(frozenset({x["hazard"], y["hazard"]}))
                if "lift" in (x["hazard"], y["hazard"]) and not say:
                    say = "work under a lift"
                if say:
                    out.append({"kind": "simops", "say": (
                        f"{say[0].upper()}{say[1:]} in {area}: {x['type']} ({x['permit']}) for this job and "
                        f"{y['type']} ({y['permit']}) for “{b['title']}”")})
    return out


def at_dispatch(conn, subjects: list[str], exception_id: int | None = None, now: datetime | None = None,
                plan_item: int | None = None) -> dict:
    """Conflicts between a job about to be sent and what is already in the field or planned now: other open jobs
    sent to the field, and plan items under way or starting within SOON. Ready when there are none. A job never
    clashes with its own case, its own bars or the visit it rides along with (`plan_item`, or any bar of the
    exception). The same asset as a case a field operator already has is no clash: the field is already on it, and
    `field_cases` names that case so whoever sends this one can see it (tour E, E14)."""
    from . import exceptions

    now = now or datetime.now(UTC)
    title = case = None
    if exception_id is not None:
        row = conn.execute("select title, item_id from exception_record where id = %s", (exception_id,)).fetchone()
        title, case = (row["title"], row["item_id"]) if row else (None, None)
    from .safeguards import records

    on_file = records(conn)
    plant = structure.load(conn) if on_file else None  # once for this job and every job it is checked against
    me = job(conn, title or "this job", subjects, plant=plant, on_file=on_file)
    field = exceptions.operators(conn)
    rows = conn.execute(
        """
        select e.id, e.title, e.subjects, e.item_id, w.role, w.assignee from exception_record e
        join work_item w on w.id = e.item_id
        where e.state = 'open' and e.joined_to is null and w.state not in ('done', 'cancelled')
          and e.id is distinct from %s and e.item_id is distinct from %s
          and exists (select 1 from event_outbox o where o.topic = 'exception.dispatched' and o.ref = 'exception:' || e.id)
        order by e.id
        """, (exception_id, case)).fetchall()
    others = [job(conn, r["title"], list(r["subjects"] or []), ref={"exception": r["id"]}, plant=plant,
                  on_file=on_file) | {"field": r["role"] == exceptions.OPERATOR_ROLE or r["assignee"] in field,
                                      "item_id": r["item_id"]} for r in rows]
    # a job sent to the field that is also on the plan is one job: counted once, as sent, not again as its plan item
    # (the same clash line twice on a case, tour D16)
    sent = [o["ref"]["exception"] for o in others]
    others += [job(conn, r["title"], list(r["subjects"] or []), r["area_id"], ref={"plan_item": r["id"]}, plant=plant,
                   on_file=on_file)
               for r in conn.execute(
        """
        with mine as (select id, rides_with from plan_item
                      where id = %(item)s or (%(e)s::bigint is not null and exception_id = %(e)s))
        select id, title, subjects, area_id from plan_item
        where status in ('proposed', 'scheduled', 'in_progress') and start_at <= %(soon)s and end_at >= %(now)s
          and exception_id is distinct from %(e)s and (exception_id is null or exception_id <> all(%(sent)s))
          and (%(case)s::bigint is null or item_id is distinct from %(case)s)
          and id not in (select id from mine) and id not in (select rides_with from mine where rides_with is not null)
          and (rides_with is null or rides_with not in (select id from mine))
        order by start_at, id
        """, {"now": now, "soon": now + SOON, "e": exception_id, "sent": sent, "item": plan_item,
              "case": case}).fetchall()]
    found, there = [], []
    for o in others:
        for c in conflicts(conn, me, o):
            if c["kind"] == "same_asset" and o.get("field"):
                there.append({"exception": o["ref"]["exception"], "item_id": o["item_id"], "title": o["title"]})
            else:
                found.append({**c, "with": o["ref"], "title": o["title"]})
    return {"check": "simops", "label": "Simultaneous operations", "ready": not found,
            "blockers": [c["say"] for c in found], "conflicts": found, "field_cases": there, "checked": True,
            "note": None if found else "Nothing else in the field or planned now clashes with it."}


def on_plan(conn, rows: list[dict]) -> dict[int, list[dict]]:
    """Hazard clashes between plan items at the same time in the same area, by item id (the same asset is already a
    clash on Plan)."""
    from .plan import ACTIVE

    active = [r for r in rows if r["status"] in ACTIVE and r["area_id"]]
    out: dict[int, list[dict]] = {}
    if len(active) < 2:
        return out
    pairs = [(a, b) for i, a in enumerate(active) for b in active[i + 1:]
             if a["start_at"] < b["end_at"] and b["start_at"] < a["end_at"] and a["area_id"] == b["area_id"]]
    if not pairs:  # nothing side by side: no need to read the plant or any item's permits
        return out
    paired = {r["id"]: r for pair in pairs for r in pair}
    from .safeguards import records

    on_file = records(conn)
    # the plant is read for the bypasses' systems: once, not once per item, and not at all with none on file
    plant = structure.load(conn) if on_file and any(r["subjects"] for r in paired.values()) else None
    hazards = _hazards_of(conn, {i: list(r["subjects"] or []) for i, r in paired.items()})
    jobs = {i: job(conn, r["title"], list(r["subjects"] or []), r["area_id"], plant=plant, on_file=on_file,
                   hazards=hazards[i]) for i, r in paired.items()}
    for a, b in pairs:
        for x, y in ((a, b), (b, a)):
            for c in conflicts(conn, jobs[x["id"]], jobs[y["id"]], same_asset=False):
                out.setdefault(x["id"], []).append({"kind": "simops", "with": y["id"], "title": y["title"],
                                                    "say": c["say"]})
    return out

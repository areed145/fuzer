"""Where attention is needed (Home's map): the open exceptions, gathered at the places they are about.

A place is something with a location in the geo index. Each open exception's subjects are walked up their
part_of / installed_at / located_at chain to the nearest located platform, pad or facility (kind area or
facility), else the nearest located thing at all (a well, a pipeline), so a well's gas-lift alert lands on the
jacket an operator goes to. One exception about several places counts once at each.

Register housekeeping (work for the data steward: unmatched records, missing criticality) is counted, not
placed: it is not somewhere to send anyone.

Read only: it lists what `exceptions.listing` lists (open, not shelved, one row per event), adds each case's due
time, and says nothing it did not read. Priority is the exception's 0-1 score, banded for display:
high >= 0.8, medium >= 0.5, low otherwise (unscored reads as low).
"""

from . import exceptions

PLACE_KINDS = ("area", "facility")
RELS = ("part_of", "installed_at", "located_at")
HIGH, MEDIUM = 0.8, 0.5
TOP = 3
DATA_ROLE = "role:data_steward"


def band(priority: float | None) -> str:
    p = priority or 0
    return "high" if p >= HIGH else "medium" if p >= MEDIUM else "low"


RANK = {"high": 0, "medium": 1, "low": 2}


def _places(conn, ids: list[str]) -> dict[str, dict]:
    """Subject -> the place it sits at: {id, kind, label, lon, lat}. Subjects with no located ancestor are left out."""
    if not ids:
        return {}
    rows = conn.execute(
        """
        with recursive up (start, fuzer_id, depth) as (
            select s, s, 0 from unnest(%(ids)s::text[]) s
            union
            select up.start, e.dst, up.depth + 1 from up
            join edge e on e.src = up.fuzer_id and e.rel = any(%(rels)s) and e.valid_to is null
            where up.depth < 6)
        select distinct on (up.start) up.start, g.fuzer_id as id, g.kind, g.cx as lon, g.cy as lat,
               coalesce(l.label, g.fuzer_id) as label, coalesce(n.value, d.value) #>> '{}' as name
        from up join geo_index g on g.fuzer_id = up.fuzer_id
        left join entity_label l on l.fuzer_id = g.fuzer_id
        left join golden n on n.fuzer_id = g.fuzer_id and n.field = 'gis_name'
        left join golden d on d.fuzer_id = g.fuzer_id and d.field = 'description'
        order by up.start, g.kind <> all(%(places)s), up.depth
        """, {"ids": ids, "rels": list(RELS), "places": list(PLACE_KINDS)}).fetchall()
    return {r["start"]: dict(r) for r in rows}


def only_at(conn, rows: list[dict], place: str) -> list[dict]:
    """The rows (an exception listing) that `hotspots` gathers at this place: All work's "at a place" filter."""
    located = _places(conn, sorted({s for r in rows for s in r["subjects"]}))
    return [r for r in rows if any(located.get(s, {}).get("id") == place for s in r["subjects"])]


def _dues(conn, item_ids: list[int]) -> dict[int, dict]:
    """Each case's due time (its open person step's, else the case's) and whether it is past it."""
    if not item_ids:
        return {}
    rows = conn.execute(
        """
        select w.id, coalesce(t.due_at, w.due) as due, coalesce(t.due_at, w.due) < now() as overdue
        from work_item w
        left join lateral (select due_at from work_token where item_id = w.id and state = 'open' and due_at is not null
                           order by due_at limit 1) t on true
        where w.id = any(%s)
        """, (item_ids,)).fetchall()
    return {r["id"]: r for r in rows}


def _crew(row: dict) -> dict | None:
    """Who is out on it: the last field report saying someone is on site, else the crew planned for it."""
    field = [f for f in ((row.get("evidence") or {}).get("field") or []) if f.get("status") in ("on_site", "done")]
    if field and field[-1]["status"] == "on_site":
        return {"who": field[-1].get("by"), "status": "on site", "at": field[-1].get("at")}
    planned = row.get("planned")
    if planned and planned.get("crew"):
        return {"who": planned["crew"], "status": "planned", "at": planned.get("start_at")}
    return None


def hotspots(conn, limit: int = 1000) -> dict:
    """The places with open exceptions, most urgent first (highest band, then overdue, then how many), each with
    its counts, its top items and any crew out there; plus how many open exceptions could not be placed."""
    from ..core import exceptions as contract

    contract.load()  # every declared type, so labels and roles resolve
    rows = exceptions.listing(conn, state=exceptions.OPEN, limit=limit)
    kinds = exceptions.types(conn)
    data_work = sum(1 for r in rows if getattr(kinds.get(r["type"]), "role", None) == DATA_ROLE)
    rows = [r for r in rows if getattr(kinds.get(r["type"]), "role", None) != DATA_ROLE]
    located = _places(conn, sorted({s for r in rows for s in r["subjects"]}))
    dues = _dues(conn, sorted({r["item_id"] for r in rows if r["item_id"]}))
    labels = {n: t.label for n, t in kinds.items()}
    places: dict[str, dict] = {}
    unplaced = 0
    for r in rows:
        at = {located[s]["id"]: located[s] for s in r["subjects"] if s in located}
        if not at:
            unplaced += 1
            continue
        due = dues.get(r["item_id"]) or {}
        crew = _crew(r)
        item = {"id": r["id"], "title": r["title"], "type": r["type"], "label": labels.get(r["type"], r["type"].replace("_", " ")),
                "source": r["source"], "priority": r["priority"], "band": band(r["priority"]),
                "value_usd": r["value_usd"], "assignee": r["assignee"], "role": r["role"], "item_id": r["item_id"],
                "due": due.get("due"), "overdue": bool(due.get("overdue")), "first_seen": r["first_seen"], "crew": crew}
        for pid, p in at.items():
            spot = places.setdefault(pid, {"id": pid, "label": p["label"], "name": p["name"], "kind": p["kind"], "lon": p["lon"],
                                           "lat": p["lat"], "items": [], "crews": []})
            spot["items"].append(item)
            if crew and crew not in spot["crews"]:
                spot["crews"].append(crew)
    out = []
    for spot in places.values():
        items = sorted(spot.pop("items"), key=lambda i: (RANK[i["band"]], not i["overdue"], -(i["priority"] or 0)))
        counts = {b: sum(i["band"] == b for i in items) for b in RANK}
        out.append({**spot, "band": items[0]["band"], "open": len(items), "counts": counts,
                    "overdue": sum(i["overdue"] for i in items),
                    "unassigned": sum(not i["assignee"] for i in items),
                    "top": items[:TOP], "exceptions": [i["id"] for i in items]})
    out.sort(key=lambda s: (RANK[s["band"]], -s["overdue"], -s["open"], s["label"]))
    return {"places": out, "open": len(rows), "unplaced": unplaced, "data_work": data_work,
            "truncated": len(rows) + data_work == limit}

"""A schematic scene of a facility or an area (P5.16a): what is there, as generic shapes on plates, with the links
between them as pipe runs. For reading flow and scenarios, not a model of the plant: nothing is to scale, and where
the register holds no geometry every position comes from the links alone.

    scene(conn, at=None, rels=None, pack=None)   {root, focus, plates, items, links, shapes, counts, places}

`at` is a facility, an area, or anything inside one (a tag, a well, an item of equipment: the scene is its area, and
`focus` is the drawn item it stands for). With no `at` the scene is the facility with the most areas.

Read from the register, never from names:
    plates   the areas under the root (part_of), each a plate; its frame from a word in the area's own type
    items    what is directly in an area (part_of, located_at), each with a shape by ISO 14224 class, process type or
             kind (core/shapes.py; a bundle overrides the table). Small things (valves, instruments) are drawn only
             when a link ends on them; the rest are counted, never silently dropped
    links    the ontology's flow view by default (flows_to, lift_gas_to, pipes_to), with what each carries. An end in
             another area, or in none, is still drawn, on a plate of its own. A shaft (drives) is never a run

The layout is deterministic. In a plate, flow runs along x: an item's column is its longest path from the plate's first
items. Parallel runs sit side by side along z: a header (an item feeding, or fed by, two or more items of one shape)
is left out, and each group still joined is a lane (valves and instruments don't make a header). A header sits across the lanes it joins; a driver sits beside
what it drives (drives); items on no link are set out in rows behind. Plates are packed in rows.
"""

from collections import defaultdict
from statistics import mean

from ..core import shapes
from . import queries

CONTAINED = ("part_of", "located_at")
CLIMB = 5  # steps from an item up to its area
CELL_X, CELL_Z = 8.0, 5.0  # one column of flow, one lane
MARGIN = 4.0
PLATE_GAP = 10.0
NOZZLE, RACK, BRIDGE = 1.0, 9.0, 12.0  # heights of a run between neighbours, across a plate, between plates
LOOSE_PER_ROW = 8
MAX_ITEMS = 3000
ELSEWHERE, UNPLACED = "elsewhere", "unplaced"
COUPLING = "drives"


def _rows(conn, sql: str, *args) -> list[dict]:
    return conn.execute(sql, args).fetchall()


def _root(conn, at: str | None) -> tuple[dict | None, str | None]:
    """The facility or area the scene is of, and the item inside it that was asked for."""
    if at is None:
        row = conn.execute(
            """
            select f.fuzer_id as id, f.kind from entity f
            left join edge p on p.dst = f.fuzer_id and p.rel = 'part_of' and p.valid_to is null
            left join entity_label l on l.fuzer_id = f.fuzer_id
            where f.kind in ('facility', 'area') and f.merged_into is null
            group by f.fuzer_id, f.kind, l.label
            order by (f.kind = 'facility') desc, count(p.id) desc, l.label limit 1
            """).fetchone()
        return (row, None) if row else (None, None)
    cur, focus = at, None
    for _ in range(CLIMB):
        row = conn.execute("select fuzer_id as id, kind from entity where fuzer_id = %s", (cur,)).fetchone()
        if row is None:
            return None, None
        if row["kind"] in ("facility", "area"):
            return row, focus
        up = conn.execute(
            """
            select e.dst from edge e join entity d on d.fuzer_id = e.dst
            where e.src = %s and e.rel = any(%s) and e.valid_to is null
            order by (d.kind = 'area') desc, (e.rel = 'installed_at') desc, e.dst limit 1
            """, (cur, [*CONTAINED, "installed_at"])).fetchone()
        if up is None:
            return None, None
        focus, cur = cur, up["dst"]
    return None, None


def _areas(conn, root: dict) -> list[str]:
    if root["kind"] == "area":
        return [root["id"]]
    rows = _rows(conn, """
        with recursive under(id) as (
            select %s::text
            union
            select e.src from edge e join under u on e.dst = u.id join entity a on a.fuzer_id = e.src
            where e.rel = 'part_of' and e.valid_to is null and a.kind = 'area' and a.merged_into is null
        ) select id from under where id <> %s""", root["id"], root["id"])
    return [r["id"] for r in rows]


def _describe(conn, ids: list[str]) -> dict[str, dict]:
    """Each entity's kind, label, description, process type and ISO 14224 classes (its own or its equipment's)."""
    out = {r["id"]: {**r, "classes": []} for r in _rows(conn, """
        select en.fuzer_id as id, en.kind, l.label, d.value #>> '{}' as description, t.value #>> '{}' as type,
               ft.value #>> '{}' as area_type
        from entity en
        left join entity_label l using (fuzer_id)
        left join golden d on d.fuzer_id = en.fuzer_id and d.field = 'description'
        left join golden t on t.fuzer_id = en.fuzer_id and t.field = 'ec_type'
        left join golden ft on ft.fuzer_id = en.fuzer_id and ft.field = 'facility_type'
        where en.fuzer_id = any(%s)""", ids)}
    for r in _rows(conn, """
        with of (id, src) as (  -- its own class and that of the equipment installed at it: two index walks
            select t.id, t.id from unnest(%s::text[]) t(id)
            union
            select i.dst, i.src from edge i
            where i.dst = any(%s::text[]) and i.rel = 'installed_at' and i.valid_to is null)
        select distinct o.id, cl.label from of o
        join edge c on c.src = o.src and c.rel = 'classified_as' and c.valid_to is null
        join entity_label cl on cl.fuzer_id = c.dst order by cl.label""", ids, ids):
        code = r["label"].split(":", 1)[-1]
        if code not in out[r["id"]]["classes"]:
            out[r["id"]]["classes"].append(code)
    return out


def _area_of(conn, ids: list[str]) -> dict[str, str]:
    """The area each of these is in, directly or through what it is part of."""
    out: dict[str, str] = {}
    todo = {i: i for i in ids}  # where the climb is now -> who is climbing
    at = {i: i for i in ids}
    for _ in range(CLIMB):
        if not at:
            break
        rows = _rows(conn, """
            select distinct on (e.src) e.src, e.dst, d.kind from edge e join entity d on d.fuzer_id = e.dst
            where e.src = any(%s) and e.rel = any(%s) and e.valid_to is null
            order by e.src, (d.kind = 'area') desc, e.dst""", list(set(at.values())), [*CONTAINED, "installed_at"])
        up = {r["src"]: r for r in rows}
        nxt = {}
        for who, cur in at.items():
            if (r := up.get(cur)) is None:
                continue
            if r["kind"] == "area":
                out[who] = r["dst"]
            else:
                nxt[who] = r["dst"]
        at = nxt
    del todo
    return out


def _acyclic(nodes: list[str], succ: dict[str, list[str]]) -> dict[str, list[str]]:
    """The links with any that close a loop left out, walking in a fixed order, so columns can be counted."""
    state: dict[str, int] = {}
    kept: dict[str, list[str]] = defaultdict(list)
    for start in nodes:
        if start in state:
            continue
        stack = [(start, iter(succ.get(start, ())))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            for nxt in it:
                if state.get(nxt) == 1:
                    continue  # closes a loop: a recycle
                kept[node].append(nxt)
                if nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(succ.get(nxt, ()))))
                    break
            else:
                state[node] = 2
                stack.pop()
    return kept


def _columns(nodes: list[str], succ: dict[str, list[str]]) -> dict[str, int]:
    """Each item's column: its longest path from the first items along the links."""
    dag = _acyclic(nodes, succ)
    indeg = {n: 0 for n in nodes}
    for a in dag:
        for b in dag[a]:
            indeg[b] += 1
    col = {n: 0 for n in nodes}
    todo = [n for n in nodes if indeg[n] == 0]
    while todo:
        a = todo.pop(0)
        for b in dag.get(a, ()):
            col[b] = max(col[b], col[a] + 1)
            indeg[b] -= 1
            if indeg[b] == 0:
                todo.append(b)
    return col


def _layout(ids: list[str], local: list[tuple[str, str]], drives: list[tuple[str, str]], shape: dict[str, str],
            label: dict[str, str], small: set[str]) -> tuple[dict[str, tuple[int, float]], dict[str, str]]:
    """Cells (column, lane) for a plate's items, and how each was placed."""
    by_label = lambda n: (label[n], n)
    ids = sorted(ids, key=by_label)
    succ: dict[str, set[str]] = defaultdict(set)
    pred: dict[str, set[str]] = defaultdict(set)
    for a, b in local:
        succ[a].add(b)
        pred[b].add(a)
    linked = [n for n in ids if succ.get(n) or pred.get(n)]
    col = _columns(linked, {n: sorted(succ[n], key=by_label) for n in linked})

    def fans(n: str) -> bool:
        for nbrs in (succ, pred):
            seen: dict[str, int] = defaultdict(int)
            for x in nbrs.get(n, ()):
                if x not in small:  # a machine with two meters on its outlet is no header
                    seen[shape[x]] += 1
            if any(v >= 2 for v in seen.values()):
                return True
        return False

    headers = {n for n in linked if fans(n)}
    lane_of: dict[str, int] = {}
    lanes: list[list[str]] = []
    for n in linked:  # a lane: what is still joined once the headers are left out
        if n in headers or n in lane_of:
            continue
        group, todo = [], [n]
        while todo:
            x = todo.pop()
            if x in lane_of or x in headers:
                continue
            lane_of[x] = len(lanes)
            group.append(x)
            todo += sorted((succ.get(x, set()) | pred.get(x, set())) - headers, key=by_label)
        lanes.append(sorted(group, key=by_label))

    cell: dict[str, tuple[int, float]] = {}
    how: dict[str, str] = {}
    taken: set[tuple[int, float]] = set()

    def put(n: str, c: int, z: float, why: str) -> None:
        cell[n] = (c, z)
        taken.add((c, z))
        how[n] = why

    driven: dict[str, list[str]] = defaultdict(list)  # driver -> what it turns, for drivers on no link of their own
    for a, b in drives:
        if a in shape and b in lane_of and a not in lane_of and a not in headers:
            driven[a].append(b)
    base = 0.0
    lane_z: dict[int, float] = {}
    for i, group in enumerate(lanes):
        lane_z[i] = base
        depth: dict[int, int] = defaultdict(int)
        for n in group:
            put(n, col[n], base + depth[col[n]], "flow order")
            depth[col[n]] += 1
        rows = max(depth.values())
        mine = sorted((d for d, turns in driven.items() if lane_of[min(turns, key=by_label)] == i), key=by_label)
        for d in mine:  # a driver beside what it turns, in a row of the lane's own
            c = round(mean(col[x] for x in driven[d]))
            while (c, base + rows) in taken:
                c += 1
            put(d, c, base + rows, "beside what it drives")
        base += rows + (1 if mine else 0)
    span = max(base - 1, 0.0)
    for n in sorted(headers, key=lambda n: (col[n], *by_label(n))):  # a header across the lanes it joins
        near = [cell[x][1] for x in sorted(succ.get(n, set()) | pred.get(n, set()), key=by_label) if x in cell]
        want = float(round(mean(near))) if near else span / 2
        c = col[n]
        if (c, want) in taken and not succ.get(n):
            c += 1  # nothing downstream of it here: one column on keeps it clear of the lanes' last items
        z = next(want + s * k for k in range(len(ids) + 2) for s in (1, -1) if (c, want + s * k) not in taken)
        put(n, c, z, "flow order")
    behind = max((z for _, z in taken), default=-1.0) + 1
    low = min((z for _, z in taken), default=0.0)
    loose = [n for n in ids if n not in cell]
    loose.sort(key=lambda n: (shape[n], *by_label(n)))
    for i, n in enumerate(loose):
        put(n, i % LOOSE_PER_ROW, behind + i // LOOSE_PER_ROW, "set out in rows: on no link here")
    if low < 0:
        cell = {n: (c, z - low) for n, (c, z) in cell.items()}
    return cell, how


def _frame(table: dict, area_type: str | None) -> str:
    words = (area_type or "").lower().replace("-", " ").split()
    return next((table["frames"][w] for w in words if w in table["frames"]), "slab")


def _route(a: dict, b: dict, lift: float) -> list[list[float]]:
    """A pipe run from a to b: straight along the flow, through the gap between columns, or up and over."""
    ax, az, bx, bz = a["x"], a["z"], b["x"], b["z"]
    ya, yb = a["y"] + NOZZLE, b["y"] + NOZZLE
    if a["plate"] == b["plate"] and abs((bx - ax) - CELL_X) < 0.01:
        if az == bz:
            return [[ax, ya, az], [bx, yb, bz]]
        mid = (ax + bx) / 2
        return [[ax, ya, az], [mid, ya, az], [mid, yb, bz], [bx, yb, bz]]
    top = max(a["y"], b["y"]) + (RACK if a["plate"] == b["plate"] else BRIDGE) + lift
    pts = [[ax, ya, az], [ax, top, az]]
    if az != bz and ax != bx:
        turn = bx - CELL_X / 2 if bx > ax else bx + CELL_X / 2
        pts += [[turn, top, az], [turn, top, bz]]
    pts += [[bx, top, bz], [bx, yb, bz]]
    return [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]


def places(conn) -> list[dict]:
    """The facilities and areas a scene can be of, for the picker."""
    return _rows(conn, """
        select en.fuzer_id as id, en.kind, l.label, p.dst as parent from entity en
        left join entity_label l using (fuzer_id)
        left join lateral (select e.dst from edge e join entity d on d.fuzer_id = e.dst
                           where e.src = en.fuzer_id and e.rel = 'part_of' and e.valid_to is null
                             and d.kind in ('facility', 'area') order by e.dst limit 1) p on true
        where en.kind in ('facility', 'area') and en.merged_into is null
          and (en.kind = 'area' or exists (select 1 from edge e where e.dst = en.fuzer_id and e.rel = 'part_of'))
        order by (en.kind = 'facility') desc, l.label""")


def scene(conn, at: str | None = None, rels: list[str] | None = None, pack=None) -> dict:
    table = shapes.for_pack(pack)
    # A shaft is not a pipe run: what drives what sets a driver beside its machine instead.
    rels = [r for r in dict.fromkeys(rels or queries.network_rels(conn)) if r != COUPLING]
    root, focus = _root(conn, at)
    empty = {"root": None, "focus": None, "rels": rels, "plates": [], "items": [], "links": [], "shapes": {},
             "counts": {"items": 0, "links": 0, "not_drawn": 0, "by_layout": 0, "by_geometry": 0, "no_shape": 0},
             "truncated": False, "places": places(conn)}
    if root is None:
        return empty | {"basis": "not in an area" if at else "no facility or area is loaded"}
    areas = _areas(conn, root)
    member_rows = _rows(conn, """
        select distinct on (e.src) e.src as id, e.dst as area from edge e join entity en on en.fuzer_id = e.src
        where e.rel = any(%s) and e.dst = any(%s) and e.valid_to is null
          and en.merged_into is null and en.retired_at is null and en.kind <> 'area'
        order by e.src, e.rel desc, e.dst""", list(CONTAINED), areas)
    home = {r["id"]: r["area"] for r in member_rows}
    edges = _rows(conn, """
        select e.id, e.src, e.rel, e.dst, e.props ->> 'product' as product from edge e
        where e.rel = any(%s) and e.src <> e.dst and (e.src = any(%s) or e.dst = any(%s))
          and (e.valid_from is null or e.valid_from <= now()) and (e.valid_to is null or e.valid_to > now())
        order by e.id""", rels, list(home), list(home)) if home else []
    ends = {x for e in edges for x in (e["src"], e["dst"])}
    away = sorted(ends - set(home))
    about = _describe(conn, [*home, *away, *areas, root["id"]])
    away = [n for n in away if n in about]
    away_area = _area_of(conn, away)
    area_label = {r["id"]: r["label"] for r in _rows(
        conn, "select fuzer_id as id, label from entity_label where fuzer_id = any(%s)",
        list(set(away_area.values()) | set(areas)))}

    shape = {n: shapes.pick(table, about[n]["classes"], about[n]["type"], about[n]["kind"]) for n in [*home, *away]}
    drawn = {n for n in home if not table["shapes"][shape[n]].get("detail") or n in ends} | set(away)
    truncated = len(drawn) > MAX_ITEMS
    if truncated:
        drawn = set(sorted(drawn, key=lambda n: (n not in ends, about[n]["label"] or n, n))[:MAX_ITEMS])
    for n in away:
        home[n] = ELSEWHERE if n in away_area else UNPLACED
    edges = [e for e in edges if e["src"] in drawn and e["dst"] in drawn]
    label = {n: about[n]["label"] or n for n in drawn}
    drives = [(r["src"], r["dst"]) for r in _rows(
        conn, "select src, dst from edge where rel = %s and valid_to is null and src = any(%s) and dst = any(%s) "
              "order by src, dst", COUPLING, list(drawn), list(drawn))]

    small = {n for n in drawn if table["shapes"][shape[n]].get("detail")}
    plate_ids = [a for a in areas if any(home[n] == a for n in drawn)] or areas[:1]
    plate_ids.sort(key=lambda a: (area_label.get(a) or a, a))
    plate_ids += [p for p in (ELSEWHERE, UNPLACED) if any(home[n] == p for n in drawn)]
    names = {ELSEWHERE: "Connected elsewhere", UNPLACED: "In no area"}
    plates, items = [], {}
    for pid in plate_ids:
        mine = [n for n in drawn if home[n] == pid]
        inside = set(mine)
        local = sorted({(e["src"], e["dst"]) for e in edges if e["src"] in inside and e["dst"] in inside})
        cell, how = _layout(mine, local, drives, shape, label, small)
        cols = max((c for c, _ in cell.values()), default=0)
        lanes = max((z for _, z in cell.values()), default=0.0)
        real = pid not in names
        plates.append({"id": pid, "label": area_label.get(pid) or pid if real else names[pid], "area": real,
                       "frame": _frame(table, about[pid]["area_type"]) if real else "slab",
                       "type": about[pid]["area_type"] if real else None,
                       "w": cols * CELL_X + 2 * MARGIN + CELL_X / 2, "d": lanes * CELL_Z + 2 * MARGIN + CELL_Z / 2,
                       "items": len(mine)})
        for n in mine:
            a = about[n]
            items[n] = {"id": n, "label": label[n], "kind": a["kind"], "description": a["description"],
                        "classes": a["classes"], "shape": shape[n], "plate": pid,
                        "cell": cell[n], "placed": how[n],
                        **({"area": area_label.get(away_area[n])} if n in away_area else {})}
    # Plates in rows, in a fixed order, to about one and a half times as wide as deep.
    limit = max((sum(p["w"] * p["d"] for p in plates) * 1.5) ** 0.5, max((p["w"] for p in plates), default=0.0))
    x = z = depth = 0.0
    for p in plates:
        if x and x + p["w"] > limit:
            x, z, depth = 0.0, z + depth + PLATE_GAP, 0.0
        p["x"], p["y"], p["z"] = x, 0.0, z
        x += p["w"] + PLATE_GAP
        depth = max(depth, p["d"])
    at_plate = {p["id"]: p for p in plates}
    for it in items.values():
        p, (c, lane) = at_plate[it["plate"]], it.pop("cell")
        it["x"] = p["x"] + MARGIN + CELL_X / 4 + c * CELL_X
        it["z"] = p["z"] + MARGIN + CELL_Z / 4 + lane * CELL_Z
        it["y"] = p["y"]
    lift = {r: 0.5 * i for i, r in enumerate(rels)}
    links = [{"id": e["id"], "src": e["src"], "dst": e["dst"], "rel": e["rel"], "product": e["product"],
              "path": _route(items[e["src"]], items[e["dst"]], lift[e["rel"]])} for e in edges]
    used = sorted({it["shape"] for it in items.values()})
    out_focus = focus if focus in items else None
    return {
        "root": {"id": root["id"], "kind": root["kind"], "label": about[root["id"]]["label"] or root["id"]},
        "focus": out_focus, "rels": rels, "plates": plates,
        "items": sorted(items.values(), key=lambda it: (it["label"], it["id"])), "links": links,
        "shapes": {s: table["shapes"][s] for s in used},
        "counts": {"items": len(items), "links": len(links),
                   "not_drawn": sum(1 for n in home if n not in drawn),
                   "by_layout": len(items), "by_geometry": 0,
                   "no_shape": sum(1 for it in items.values() if it["shape"] == shapes.FALLBACK)},
        "truncated": truncated, "places": places(conn),
    }

"""Plant structure (P2.7): trains, flow order, a stage's scrubber and cooler, parallel trains and drivers, read from
the register alone, never from tag names.

    piping   pipes_to (P&IDs and the engineering line list), walked through instruments and valves to the next item
             of equipment (a tag with an ISO 14224 class of its own or installed at it)
    area     the area each tag is part of (part_of, up to the first area)
    drivers  drives (a turbine, engine or motor and the machine it turns, from the driven item's equipment record)

A train is the piped equipment of one area once its headers are left out, plus whatever drives it. A header is an
item that feeds, or is fed by, two or more items of one class in its area (the inlet separator feeding three
compression trains, the drum feeding two spared pumps); the headers next to a train are its `shared` items. Flow
order is the distance from the train's first items along its piping.

    load(conn)                   the plant's structure, to pass as `plant=` when asking about many items
    train(conn, fid)             {known, basis, key, name, area, members, shared, machines}
    upstream / downstream(conn, fid, classes)   the nearest item of those classes along the piping, inside its train
    stage(conn, fid)             {known, basis, train, order, scrubber, cooler_before, siblings}
    driver(conn, fid)            what turns it (drives), or None

Where the data can't answer (no piping in the area, a machine on none of it), `known` is false and `basis` says why;
nothing is guessed.
"""

from collections import deque
from dataclasses import dataclass, field

COMPRESSOR, VESSEL, EXCHANGER = "CO", "VE", "HE"
AREA_DEPTH = 3  # part_of steps from a tag up to its area


def _code(cls: str | None) -> str | None:
    return cls.split(":", 1)[-1].split(".", 1)[0] if cls else None


@dataclass
class Plant:
    labels: dict[str, str] = field(default_factory=dict)
    classes: dict[str, set[str]] = field(default_factory=dict)  # equipment only
    area: dict[str, str] = field(default_factory=dict)
    succ: dict[str, set[str]] = field(default_factory=dict)  # equipment to equipment, through instruments and valves
    pred: dict[str, set[str]] = field(default_factory=dict)
    drives: dict[str, str] = field(default_factory=dict)  # driven -> driver
    headers: set[str] = field(default_factory=set)
    trains: dict[str, frozenset[str]] = field(default_factory=dict)  # member -> its train's piped members

    def label(self, fid: str | None) -> str | None:
        return self.labels.get(fid, fid) if fid else None


def load(conn) -> Plant:
    """Read the plant's piping, classes, areas and drivers once."""
    p = Plant()
    p.labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select l.fuzer_id, l.label from entity_label l join entity e using (fuzer_id) "
        "where e.kind in ('tag', 'area') and e.merged_into is null")}
    for r in conn.execute(
        """
        with of (tag, src) as (  -- a tag's own class and that of the equipment installed at it: two index walks
            select t.fuzer_id, t.fuzer_id from entity t where t.kind = 'tag' and t.merged_into is null
            union
            select t.fuzer_id, i.src from entity t
            join edge i on i.dst = t.fuzer_id and i.rel = 'installed_at' and i.valid_to is null
            where t.kind = 'tag' and t.merged_into is null)
        select distinct o.tag as fuzer_id, cl.label from of o
        join edge c on c.src = o.src and c.rel = 'classified_as'
        join entity_label cl on cl.fuzer_id = c.dst
        """):
        if code := _code(r["label"]):
            p.classes.setdefault(r["fuzer_id"], set()).add(code)
    parent: dict[str, str] = {}
    kind = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where kind in ('tag', 'area') and merged_into is null")}
    for r in conn.execute("select src, dst from edge where rel = 'part_of' and valid_to is null order by dst"):
        if r["src"] in kind and r["dst"] in kind:
            parent.setdefault(r["src"], r["dst"])
    for t in kind:
        x = t
        for _ in range(AREA_DEPTH):
            x = parent.get(x)
            if x is None:
                break
            if kind.get(x) == "area":
                p.area[t] = x
                break
    pipes: dict[str, set[str]] = {}
    for r in conn.execute("select src, dst from edge where rel = 'pipes_to' and valid_to is null"):
        if r["src"] != r["dst"]:
            pipes.setdefault(r["src"], set()).add(r["dst"])
    for a in [t for t in pipes if t in p.classes]:  # walk through instruments and valves to the next equipment
        seen, todo = {a}, deque(pipes.get(a, ()))
        while todo:
            b = todo.popleft()
            if b in seen:
                continue
            seen.add(b)
            if b in p.classes:
                p.succ.setdefault(a, set()).add(b)
                p.pred.setdefault(b, set()).add(a)
            else:
                todo.extend(pipes.get(b, ()))
    for r in conn.execute("select src, dst from edge where rel = 'drives' and valid_to is null order by src"):
        p.drives.setdefault(r["dst"], r["src"])
    _trains(p)
    return p


def _local(p: Plant, n: str, nbrs: dict[str, set[str]]) -> set[str]:
    return {x for x in nbrs.get(n, ()) if p.area.get(x) is not None and p.area.get(x) == p.area.get(n)}


def _trains(p: Plant) -> None:
    for n in p.classes:
        for nbrs in (p.succ, p.pred):
            by_class: dict[frozenset, int] = {}
            for x in _local(p, n, nbrs):
                k = frozenset(p.classes[x])
                by_class[k] = by_class.get(k, 0) + 1
            if any(v >= 2 for v in by_class.values()):
                p.headers.add(n)
    piped = {n for n in p.classes if (p.succ.get(n) or p.pred.get(n)) and n in p.area and n not in p.headers}
    for n in sorted(piped):
        if n in p.trains:
            continue
        group, todo = set(), [n]
        while todo:
            x = todo.pop()
            if x in group:
                continue
            group.add(x)
            todo += [y for y in _local(p, x, p.succ) | _local(p, x, p.pred) if y in piped]
        frozen = frozenset(group)
        for x in group:
            p.trains[x] = frozen


def _order(p: Plant, members: frozenset[str]) -> dict[str, int]:
    """Each member's distance from the train's first items (those nothing in the train feeds) along its piping."""
    entries = sorted((m for m in members if not (p.pred.get(m, set()) & members)), key=lambda m: p.label(m)) \
        or [min(members, key=lambda m: p.label(m))]
    depth = {m: 0 for m in entries}
    todo = deque(entries)
    while todo:
        x = todo.popleft()
        for y in sorted(p.succ.get(x, set()) & members, key=lambda m: p.label(m)):
            if y not in depth:
                depth[y] = depth[x] + 1
                todo.append(y)
    for m in members:  # a loop no entry reaches (a recycle on its own)
        depth.setdefault(m, max(depth.values(), default=0) + 1)
    return depth


def _unknown(why: str, **extra) -> dict:
    return {"known": False, "basis": why, **extra}


def _piped_of(p: Plant, fid: str) -> str | None:
    """The piped item a tag stands for: itself, or the machine it drives (a turbine has no piping of its own)."""
    if fid in p.trains:
        return fid
    driven = sorted((d for d, drv in p.drives.items() if drv == fid and d in p.trains), key=lambda m: p.label(m))
    return driven[0] if driven else None


def train(conn, fid: str, plant: Plant | None = None) -> dict:
    """The train an item belongs to, from piping: its members (and their drivers), the headers it shares and its
    compressors. `known` is false, with the reason in `basis`, where the piping can't say."""
    p = plant or load(conn)
    if fid in p.headers:
        return _unknown(f"{p.label(fid)} is a header that several trains share, in none of them")
    at = _piped_of(p, fid)
    if not at:
        if fid not in p.classes:
            return _unknown(f"{p.label(fid)} is not an item of equipment")
        if fid not in p.area:
            return _unknown(f"{p.label(fid)} is in no area")
        if not any(p.succ.get(x) or p.pred.get(x) for x in p.classes if p.area.get(x) == p.area[fid]):
            return _unknown(f"no piping is known in {p.label(p.area[fid])}")
        return _unknown(f"{p.label(fid)} is on none of the piping in {p.label(p.area[fid])}")
    piped = p.trains[at]
    depth = _order(p, piped)
    flow = sorted(piped, key=lambda m: (depth[m], p.label(m)))
    drivers = sorted({p.drives[m] for m in piped if m in p.drives} - piped, key=lambda m: p.label(m))
    members = flow + drivers
    shared = sorted({h for m in piped for h in (p.pred.get(m, set()) | p.succ.get(m, set())) if h in p.headers},
                    key=lambda m: p.label(m))
    area = p.area[at]
    first = flow[0]
    return {"known": True, "key": f"{p.label(area)}:{p.label(first)}",
            "name": f"train from {p.label(first)} ({p.label(area)})", "area": area, "members": members,
            "shared": shared, "machines": [m for m in flow if COMPRESSOR in p.classes.get(m, set())],
            "basis": f"piping (pipes_to) among {len(piped)} items in {p.label(area)}"
                     + (f", leaving out the shared {', '.join(p.label(h) for h in shared)}" if shared else "")
                     + (f"; driven by {', '.join(p.label(d) for d in drivers)}" if drivers else "")}


def _nearest(p: Plant, fid: str, classes: set[str], nbrs: dict[str, set[str]], stop: set[str] = frozenset()) -> str | None:
    members = p.trains.get(fid)
    if not members:
        return None
    seen, todo = {fid}, deque([fid])
    while todo:
        x = todo.popleft()
        for y in sorted(nbrs.get(x, set()) & members, key=lambda m: p.label(m)):
            if y in seen:
                continue
            seen.add(y)
            if p.classes.get(y, set()) & classes:
                return y
            if not (p.classes.get(y, set()) & stop):
                todo.append(y)
    return None


def upstream(conn, fid: str, classes: set[str] | str, plant: Plant | None = None) -> str | None:
    """The nearest item of those ISO 14224 classes upstream along the piping, inside the item's train."""
    p = plant or load(conn)
    return _nearest(p, fid, {classes} if isinstance(classes, str) else set(classes), p.pred)


def downstream(conn, fid: str, classes: set[str] | str, plant: Plant | None = None) -> str | None:
    """The nearest item of those ISO 14224 classes downstream along the piping, inside the item's train."""
    p = plant or load(conn)
    return _nearest(p, fid, {classes} if isinstance(classes, str) else set(classes), p.succ)


def driver(conn, fid: str, plant: Plant | None = None) -> str | None:
    """What turns a machine: its driver (drives), or None when no source says."""
    p = plant or load(conn)
    return p.drives.get(fid)


def _position(p: Plant, fid: str) -> tuple[frozenset, int]:
    """An item's place in its train: its classes and how many items of those classes come before it in flow order."""
    members = p.trains[fid]
    depth = _order(p, members)
    same = sorted((m for m in members if p.classes.get(m) == p.classes.get(fid)), key=lambda m: (depth[m], p.label(m)))
    return frozenset(p.classes.get(fid, ())), same.index(fid)


def stage(conn, fid: str, plant: Plant | None = None) -> dict:
    """A compressor stage from piping: its order among its train's compressors, the scrubber before it (the inlet
    scrubber for the first stage), the cooler before that (none for the first stage) and the same stage on each
    parallel train fed from the same header, by train key."""
    p = plant or load(conn)
    if COMPRESSOR not in p.classes.get(fid, set()):
        return _unknown(f"{p.label(fid)} is not a compressor")
    t = train(conn, fid, p)
    if not t["known"]:
        return _unknown(t["basis"], train=None)
    stop = {COMPRESSOR}
    scrubber = _nearest(p, fid, {VESSEL}, p.pred, stop)
    cooler = _nearest(p, fid, {EXCHANGER}, p.pred, stop)
    order = t["machines"].index(fid) + 1
    pos = _position(p, fid)
    feeds = set(t["shared"])
    siblings = []
    seen = {t["key"]}
    for h in sorted(feeds, key=lambda m: p.label(m)):
        for entry in sorted(p.succ.get(h, set()) | p.pred.get(h, set()), key=lambda m: p.label(m)):
            if entry not in p.trains or p.trains[entry] == p.trains[fid]:
                continue
            other = train(conn, entry, p)
            if other["key"] in seen:
                continue
            seen.add(other["key"])
            match = [m for m in p.trains[entry] if _position(p, m) == pos]
            if match:
                siblings.append({"train": other["key"], "stage": match[0]})
    siblings.sort(key=lambda s: s["train"])
    return {"known": True, "train": t, "order": order, "scrubber": scrubber, "cooler_before": cooler,
            "siblings": [s["stage"] for s in siblings], "sibling_trains": [s["train"] for s in siblings],
            "basis": f"stage {order} of {len(t['machines'])} in flow order along the piping of the {t['name']}"}

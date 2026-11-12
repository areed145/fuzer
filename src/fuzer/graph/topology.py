"""The plant as a graph of units: what is next to what (part_of, flows_to, lift gas, P&ID piping, the members of
one train as `graph.structure` reads it from piping), and whether two units are within a few hops. Upsets (#96) and
correlation studies (#106) use it to decide what can be connected."""

HOPS = 4  # default for the setting sre.hops (#131); and on a unit this close in the plant model (compressor -> header -> manifold -> well)
LINKS = ("part_of", "flows_to", "lift_gas_to", "pipes_to")


def topology(conn) -> dict[str, set[str]]:
    """Units next to each other in the plant model: part_of (instrument -> equipment, choke -> well),
    flows_to, lift_gas_to and P&ID piping, both ways; and the members of one compression train with what drives
    them (`graph.structure`, from piping, never tag names). Areas and the facility are left out, or everything would
    touch."""
    from . import structure

    adj: dict[str, set[str]] = {}
    for r in conn.execute(
        """
        select e.src, e.dst from edge e join entity a on a.fuzer_id = e.src join entity b on b.fuzer_id = e.dst
        where e.rel = any(%s) and a.kind in ('tag', 'well', 'allocation_node') and b.kind in ('tag', 'well', 'allocation_node')
        """, (list(LINKS),)):
        adj.setdefault(r["src"], set()).add(r["dst"])
        adj.setdefault(r["dst"], set()).add(r["src"])
    tags = conn.execute(
        """
        select l.fuzer_id, l.label, (select a.dst from edge a join entity x on x.fuzer_id = a.dst and x.kind = 'area'
                                     where a.src = l.fuzer_id and a.rel = 'part_of' limit 1) as area,
               exists (select 1 from edge p join entity x on x.fuzer_id = p.dst and x.kind = 'tag'
                       where p.src = l.fuzer_id and p.rel = 'part_of') as instrument
        from entity_label l join entity e using (fuzer_id) where e.kind = 'tag'
        """).fetchall()
    plant = structure.load(conn)
    for piped in set(plant.trains.values()):
        if not any(structure.COMPRESSOR in plant.classes.get(m, set()) for m in piped):
            continue  # a machine train (its stages, scrubbers, coolers and driver), not every piped run in an area
        members = set(piped) | {plant.drives[m] for m in piped if m in plant.drives}
        if len(members) <= 20:  # a train's machines and vessels, not a whole area
            for u in members:
                adj.setdefault(u, set()).update(x for x in members if x != u)
    # A tag on its own (a header pressure, a jacket's gas lift meter) belongs with the manifolds in its area
    # and with the tags of its loop family there (20-PT-3003 with 20-FT-3002).
    nodes: dict[str, set[str]] = {}
    for r in conn.execute("select e.src, e.dst from edge e join entity n on n.fuzer_id = e.src and n.kind = 'allocation_node' "
                          "where e.rel = 'located_at'"):
        nodes.setdefault(r["dst"], set()).add(r["src"])
    family = lambda label: (label.split("-")[0], label.split("-")[-1][:2]) if label and label.count("-") >= 2 else None
    linked = {}
    for r in tags:
        if not r["instrument"] and r["fuzer_id"] in adj:
            linked.setdefault(family(r["label"]), set()).add(r["fuzer_id"])
    for r in tags:
        if r["instrument"] or r["fuzer_id"] in adj or not r["area"]:
            continue
        near = nodes.get(r["area"], set()) | linked.get(family(r["label"]), set())
        for n in near:
            adj.setdefault(r["fuzer_id"], set()).add(n)
            adj.setdefault(n, set()).add(r["fuzer_id"])
    return adj


def _near(adj: dict, a: str | None, b: str | None, cache: dict, hops: int = HOPS) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    if a not in cache:
        seen, frontier = {a}, {a}
        for _ in range(hops):
            frontier = {n for u in frontier for n in adj.get(u, ())} - seen
            seen |= frontier
        cache[a] = seen
    return b in cache[a]

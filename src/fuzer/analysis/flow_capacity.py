"""Capacity along a facility's flow network on its latest report day.

Each stage's capacity is the existing `system_oil_capacity` contract: the total facility oil
throughput that stage allows, with parallel trains, fluid composition and gas handling already
worked out. Nothing here converts a nameplate rating. A stage with no declared bound falls back to
evidence labeled for what it is, never to a claimed rating.
"""

from datetime import date, timedelta
from statistics import median

from . import production_efficiency as pe

UNIT = pe.UNIT
PEAK_DAYS = 365   # how far back a demonstrated rate still speaks for today's plant
TEST_DAYS = 90    # a well test older than this no longer says what the well makes
EXAMPLES = 5
GRADES = ["design", "statement", "moc", "tested"]  # evidence behind a bound, weakest first
SEPARATOR, MANIFOLD, COMPRESSOR, METER, SALES = "separator", "manifold", "compressor", "meter", "sales_point"
METADATA = "Dated system_oil_capacity measurement (Sm3/d, facility oil equivalent, engineering-converted)"


def _longest(nodes: dict, edges: list[tuple[str, str]], weight=lambda node: 1) -> dict:
    """Longest weighted path into each node. The pass limit bounds a cycle in the source data."""
    depth = dict.fromkeys(nodes, 0)
    for _ in range(len(nodes)):
        changed = False
        for src, dst in edges:
            step = depth[src] + weight(nodes[dst])
            if step > depth[dst]:
                depth[dst] = step
                changed = True
        if not changed:
            break
    return depth


def _reach(nodes: dict, edges: list[tuple[str, str]]) -> dict:
    """The sales points each node delivers to."""
    reach = {id: {id} if n["type"] == SALES else set() for id, n in nodes.items()}
    for _ in range(len(nodes)):
        changed = False
        for src, dst in edges:
            if not reach[dst] <= reach[src]:
                reach[src] |= reach[dst]
                changed = True
        if not changed:
            break
    return reach


def _stage_of(node: dict, separators: int, reach: set) -> tuple:
    kind = node["type"]
    if kind == SEPARATOR:
        return (SEPARATOR, separators, tuple(sorted(reach)))
    if kind in (METER, SALES):
        return ("export", tuple(sorted(reach)))
    return (kind,)


def _stage_label(key: tuple, members: list[dict]) -> str:
    kind = key[0]
    if kind == "well":
        return "Wells"
    if kind == MANIFOLD:
        return "Gathering manifolds"
    if kind == COMPRESSOR:
        return "Compression"
    if kind == SEPARATOR:
        return (members[0]["description"] or members[0]["label"]) if len(members) == 1 else "Field separators"
    if kind == "export":
        sales = [m for m in members if m["type"] == SALES]
        return " + ".join((m["description"] or m["label"]) for m in sales) or "Export"
    return str(kind or "Other").replace("_", " ").capitalize()


def stages(nodes: dict, edges: list[tuple[str, str]]) -> list[dict]:
    """Group the network into ordered stages. Parallel equipment shares a stage; separators split by
    how many separators lie upstream and by which sales points they deliver to, so a gas-only branch
    is its own stage. Order follows the members' median longest path from the wells: one member
    deep or shallow (a chained manifold, a meter close to the field) does not move its stage."""
    depth = _longest(nodes, edges)
    separators = _longest(nodes, edges, lambda node: node["type"] == SEPARATOR)
    reach = _reach(nodes, edges)
    grouped: dict[tuple, list[dict]] = {}
    for id, node in nodes.items():
        grouped.setdefault(_stage_of(node, separators[id], reach[id]), []).append(node)
    out = [{"key": key, "label": _stage_label(key, members), "members": members,
            "depth": median(depth[m["id"]] for m in members)} for key, members in grouped.items()]
    return sorted(out, key=lambda stage: (stage["depth"], stage["label"]))


def _flat(key: tuple) -> list:
    return [item for part in key for item in (part if isinstance(part, tuple) else (part,))]


def _peak(reports: list[dict]) -> tuple[float, str] | None:
    """Highest valid allocated day. A day with several reports is ambiguous, not summed."""
    by_day: dict[str, list] = {}
    for row in reports:
        by_day.setdefault(str(row["date"])[:10], []).append(pe._number(row.get("allocated")))
    days = [(values[0], day) for day, values in by_day.items() if len(values) == 1 and values[0] is not None]
    return max(days, default=None)


def _bounds(limits: list[dict]) -> dict:
    """The usable declared bound per entity, under the rules that build the dynamic maximum."""
    seen: dict[str, set] = {}
    for row in limits:
        value = pe._number(row["value"])
        ok = value is not None and row["unit"] == UNIT and not row.get("qualifier")
        seen.setdefault(row["id"], set()).add(value if ok else None)
    return {id: next(iter(values)) for id, values in seen.items() if len(values) == 1 and None not in values}


def calculate(nodes: dict, edges: list[tuple[str, str]], limits: list[dict], tests: list[dict],
              reports: list[dict], latest: dict, end: date) -> dict:
    bounds = _bounds(limits)
    peak = _peak(reports)
    tested = [row for row in tests if pe._number(row["value"]) is not None]
    tested_total = sum(float(row["value"]) for row in tested) if tested else None
    binding = set(latest["binding_constraint_ids"])
    potential = latest.get("reported_potential_sm3d") if latest["capacity_basis"] == "declared_limits" else None
    grades = {row["id"]: (row.get("method") or "").removeprefix("derived from ") for row in limits}
    grouped = stages(nodes, edges)
    placed = {m["id"] for stage in grouped for m in stage["members"]}
    labels = {row["id"]: row.get("label") or row["id"] for row in limits}
    # A declared bound off the flow network (an area, the facility) still limits the system.
    for id in sorted(set(bounds) - placed):
        grouped.append({"key": (id,), "label": labels[id], "depth": None,
                        "members": [{"id": id, "label": labels[id], "description": None, "type": "other"}]})
    rows = []
    for stage in grouped:
        members = stage["members"]
        declared = [bounds[m["id"]] for m in members if m["id"] in bounds]
        missing = [m for m in members if m["id"] not in bounds]
        if declared:
            capacity, basis = min(declared), "declared"
        elif stage["key"][0] == "well" and potential is not None:
            capacity, basis = potential, "reported_potential"
        elif stage["key"][0] == "well" and tested_total is not None:
            capacity, basis = tested_total, "well_tests"
        elif peak:
            capacity, basis = peak[0], "demonstrated"
        else:
            capacity, basis = None, "unknown"
        wells = stage["key"][0] == "well"
        complete = basis in ("declared", "reported_potential")
        rows.append({
            "id": "-".join(str(part) for part in _flat(stage["key"])), "label": stage["label"],
            "capacity_sm3d": capacity, "basis": basis,
            "grade": min((grades[m["id"]] for m in members if m["id"] in bounds),
                         key=lambda g: GRADES.index(g) if g in GRADES else len(GRADES), default=None),
            "binding": any(m["id"] in binding for m in members) or (wells and pe.WELLS["id"] in binding),
            "members": len(members), "undeclared": 0 if complete else len(missing),
            "undeclared_examples": [] if complete else [m["label"] for m in missing[:EXAMPLES]],
        })
    undeclared = [row for row in rows if row["undeclared"]]
    established = latest["capacity_basis"] == "declared_limits" and bool(rows) and not undeclared
    warnings = []
    if set(latest["flags"]) & {"invalid_capacity_limit", "ambiguous_capacity_limit"}:
        warnings.append("A declared capacity bound is invalid or ambiguous on this day; the system limit is withheld.")
    if not rows:
        warnings.append("No dated flows_to network reaches this facility on this day.")
    return {
        "as_of": end.isoformat(), "unit": UNIT, "stages": rows,
        "system": {
            "limit_sm3d": latest["dynamic_max_sm3d"], "allocated_sm3d": latest["allocated_sm3d"],
            "basis": latest["capacity_basis"], "established": established,
            "binding_stages": [row["label"] for row in rows if row["binding"]],
        },
        "demonstrated": {
            "peak_sm3d": peak[0] if peak else None, "date": peak[1] if peak else None,
            "since": (end - timedelta(days=PEAK_DAYS - 1)).isoformat(),
        },
        "well_tests": {
            "total_sm3d": tested_total, "wells": len(tested),
            "first": min((str(row["at"])[:10] for row in tested), default=None),
            "last": max((str(row["at"])[:10] for row in tested), default=None),
            "not_tested": sum(n["type"] == "well" for n in nodes.values()) - len(tested),
            "since": (end - timedelta(days=TEST_DAYS - 1)).isoformat(),
        },
        "required_metadata": METADATA, "warnings": warnings,
    }


def network(conn, facility: str, day: date) -> tuple[dict, list[tuple[str, str]]]:
    """The facility's flow network as routed on `day`: `flows_to` alone decides what is a stage."""
    params = {"facility": facility, "since": day.isoformat(), "until": (day + timedelta(days=1)).isoformat()}
    rows = conn.execute(
        pe.NETWORK + """
        , ids as (select distinct id from network)
        select e.src, e.dst from edge e join ids a on a.id = e.src join ids b on b.id = e.dst
        where e.rel = 'flows_to'
          and (e.valid_from is null or e.valid_from <= %(since)s::date)
          and (e.valid_to is null or e.valid_to > %(since)s::date)
        """, params,
    ).fetchall()
    edges = sorted({(row["src"], row["dst"]) for row in rows})
    ids = sorted({id for edge in edges for id in edge})
    nodes = {row["id"]: {**row, "type": "well" if row["kind"] == "well" else row["type"]} for row in conn.execute(
        """select e.fuzer_id as id, e.kind, coalesce(l.label, e.fuzer_id) as label,
                  d.value #>> '{}' as description, t.value #>> '{}' as type
           from entity e left join entity_label l using (fuzer_id)
           left join golden d on d.fuzer_id = e.fuzer_id and d.field = 'description'
           left join golden t on t.fuzer_id = e.fuzer_id and t.field = 'ec_type'
           where e.fuzer_id = any(%s)""", (ids,),
    ).fetchall()}
    return nodes, edges


def flow_capacity(conn, facility: str) -> dict:
    """Read the routing in force on the latest report day."""
    facility_row = conn.execute(
        """select e.fuzer_id as id, coalesce(l.label, e.fuzer_id) as label
           from entity e left join entity_label l using (fuzer_id)
           where e.fuzer_id = %s and e.kind = 'facility'
             and e.retired_at is null and e.merged_into is null""", (facility,),
    ).fetchone()
    if not facility_row:
        raise ValueError("ref must resolve to a live facility")
    end = pe.latest_report_date(conn, facility)
    if end is None:
        raise ValueError("facility has no production report")
    until = end + timedelta(days=1)
    params = {"facility": facility, "since": end.isoformat(), "until": until.isoformat()}
    nodes, edges = network(conn, facility, end)
    wells = [id for id, node in nodes.items() if node["type"] == "well"]
    tests = conn.execute(
        """select distinct on (fuzer_id) fuzer_id, at, value from measurement
           where quantity = 'oil_rate' and unit = %s and fuzer_id = any(%s)
             and at >= %s::timestamp and at < %s::timestamp
           order by fuzer_id, at desc""",
        (UNIT, wells, (end - timedelta(days=TEST_DAYS - 1)).isoformat(), until.isoformat()),
    ).fetchall()
    reports = conn.execute(
        pe.REPORTS + " and d.value #>> '{}' >= %(since)s and d.value #>> '{}' < %(until)s",
        {**params, "since": (end - timedelta(days=PEAK_DAYS - 1)).isoformat()},
    ).fetchall()
    latest = pe.production_efficiency(conn, facility, end)["points"][-1]
    return {
        **calculate(nodes, edges, pe.declared_limits(conn, params), tests, reports, latest, end),
        "facility": dict(facility_row),
        "source": "Dated flows_to network; system_oil_capacity measurements; production_day reports; latest well tests",
    }

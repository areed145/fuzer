"""Turn dated ratings into daily `system_oil_capacity` bounds.

An old asset keeps ratings in a statement, an MOC register and original datasheets, in local units
and for the item's own fluid. The bound contract needs what the whole facility could carry that day,
so the rebuild derives it: for each flow stage, the standing rating of each member, added across
parallel equipment, converted to oil with the day's fluids, and scaled up where only part of the
field passes through that stage.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime

from ..graph import queries
from . import flow_capacity as fc
from . import production_efficiency as pe

QUANTITY = "system_oil_capacity"
RATED = {"oil_capacity", "liquid_capacity", "gas_capacity", "export_gas_capacity"}
# Evidence, weakest first. Design stands only until any later register speaks; a proposal never does.
GRADES = ["design", "statement", "moc", "tested"]


def standing(ratings: list[dict], day: date) -> dict[str, dict]:
    """Per entity and quantity, the rating in force: the newest non-design entry by that day, else design."""
    out = {}
    for row in sorted((r for r in ratings if r["at"] <= day),
                      key=lambda r: (r["method"] != "design", r["at"], GRADES.index(r["method"]))):
        out[(row["id"], row["quantity"])] = row
    return out


def water_cut(rates: dict[str, tuple[float, float]]) -> float:
    oil = sum(q for q, _ in rates.values())
    water = sum(q * cut / (100 - cut) for q, cut in rates.values() if cut < 100)
    return water / (oil + water) if oil + water else 0.0


def as_oil(quantity: str, value: float, cut: float, potential: float, gas: float | None, export: float | None) -> float | None:
    """A rating as oil, at the day's water cut and gas per oil. No fluid figure, no conversion."""
    if quantity == "oil_capacity":
        return value
    if quantity == "liquid_capacity":
        return value * (1 - cut)
    stream = gas if quantity == "gas_capacity" else export
    return value * potential / stream if stream else None


def upstream_wells(members: set[str], edges: list[tuple[str, str]], nodes: dict) -> set[str]:
    feeds = defaultdict(list)
    for src, dst in edges:
        feeds[dst].append(src)
    seen, stack = set(members), list(members)
    while stack:
        for src in feeds[stack.pop()]:
            if src not in seen:
                seen.add(src)
                stack.append(src)
    return {id for id in seen if nodes[id]["type"] == "well"}


def stage_bound(stage: dict, rated: dict, share: float, oil_of) -> tuple[float, list[str], str] | None:
    """Parallel members of one type add up; types in series (a meter, its terminal) take the tighter.
    A type with an unrated member has no total, so it can't speak for the stage."""
    by_type = defaultdict(list)
    for member in stage["members"]:
        by_type[member["type"]].append(member)
    best = None
    for members in by_type.values():
        total, grades = 0.0, []
        for member in members:
            found = [(oil_of(q, row["value"]), row) for (id, q), row in rated.items() if id == member["id"]]
            found = [(value, row) for value, row in found if value is not None]
            if not found:
                break
            value, row = min(found, key=lambda pair: pair[0])
            total += value
            grades.append(row["method"])
        else:
            bound = total / share
            if best is None or bound < best[0]:
                best = (bound, [m["id"] for m in members], min(grades, key=GRADES.index))
    return best


def derive(conn, pack, rows: dict) -> dict:
    """Rows for the measurement table from the ratings and well tests about to land in it."""
    keep = [row for row in rows.values() if row[1] in RATED and row[8] in GRADES]
    if not keep:
        return {}
    facility = queries.resolve_ref(conn, pack, pack.facility)
    if facility is None:
        return {}
    _day = lambda value: date.fromisoformat(str(value)[:10])
    ratings = [{"id": r[2], "quantity": r[1], "at": _day(r[3]), "value": r[4], "method": r[8]} for r in keep]
    oil_tests, cuts = defaultdict(dict), {}
    for r in rows.values():
        if r[1] == "oil_rate":
            oil_tests[_day(r[3])][r[2]] = r[4]
        elif r[1] == "water_cut":
            cuts[(r[2], _day(r[3]))] = r[4]
    reports = conn.execute(pe.REPORTS, {"facility": facility}).fetchall()
    changes = sorted({row[key] for row in conn.execute(
        """select valid_from, valid_to from edge where rel = 'flows_to'
           and (valid_from is not null or valid_to is not null)""") for key in ("valid_from", "valid_to") if row[key]})
    networks: dict[int, tuple] = {}
    out, held = {}, {}
    tested = sorted(oil_tests)
    next_test = 0
    for report in sorted(reports, key=lambda row: row["date"]):
        day = date.fromisoformat(str(report["date"])[:10])
        while next_test < len(tested) and tested[next_test] <= day:
            when = tested[next_test]
            held |= {well: (rate, cuts.get((well, when), 0.0)) for well, rate in oil_tests[when].items()}
            next_test += 1
        potential = pe._number(report.get("potential"))
        if not potential or not held:
            continue
        span = bisect_right([date.fromisoformat(str(c)[:10]) for c in changes], day)
        if span not in networks:
            nodes, edges = fc.network(conn, facility, day)
            grouped = [s for s in fc.stages(nodes, edges) if s["key"][0] != "well"]
            ups = [upstream_wells({m["id"] for m in s["members"]}, edges, nodes) for s in grouped]
            networks[span] = (grouped, ups)
        grouped, ups = networks[span]
        rated = standing(ratings, day)
        cut = water_cut(held)
        gas, export = pe._number(report.get("gas")), pe._number(report.get("gas_export"))
        total = sum(q for q, _ in held.values())
        oil_of = lambda quantity, value: as_oil(quantity, value, cut, potential, gas, export)  # noqa: B023
        for stage, wells in zip(grouped, ups):
            through = sum(held[w][0] for w in wells if w in held)
            if not through:
                continue
            found = stage_bound(stage, rated, min(1.0, through / total), oil_of)
            if not found:
                continue
            value, members, grade = found
            for id in members:
                record = f"derived:capacity:{id}:{day}"
                out[(record, QUANTITY)] = (record, QUANTITY, id, datetime(day.year, day.month, day.day), round(value, 3),
                                           pe.UNIT, None, None, f"derived from {grade}", None)
    return out

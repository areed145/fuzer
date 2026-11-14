"""Production at risk from planned shutdowns (P5.9a, #337): over a horizon, each shutdown window on the plan with the
wells behind the units it takes down and what they would produce in that time. An estimate for planners, not a
schedule or a commitment.

    windows(conn, start, days)   the windows in [start, start + days): items that need a unit down, grouped where they
                                 overlap in time, each with its wells (counted once however many of its items take
                                 them down) and the production at risk per quantity, with the durations used

Read from the records, never from equipment type:
    a unit going down        a plan item that needs a shutdown (or is a shutdown or turnaround), on its subjects
    what flows through it    the flow network (flows_to): a subject on it, or else the flow units that share the
                             subject's parent (an aftercooler takes its train down with it)
    what the wells make      each well's latest test, per rate it reports (oil, gas, condensate), with its unit
    how long                 the item's own dates, clipped to the horizon; a source that gives a due date and no
                             outage length (SAP maintenance plans) is said to be an assumption
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .plan import ACTIVE, SHUTDOWNS, _when

RATES = ("oil_rate", "gas_rate", "condensate_rate")  # what a well test says a well makes, in the quantities it reports
ASSUMED = {"sap_plan": "SAP maintenance plans give a due date, not an outage length: one day is assumed"}
HORIZON_DAYS = 90


def _flow(conn) -> tuple[dict[str, list[str]], set[str]]:
    """What feeds each node on the flow network today, and every node on it."""
    feeds: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for r in conn.execute(
            "select src, dst from edge where rel = 'flows_to' and (valid_from is null or valid_from <= now()) "
            "and (valid_to is null or valid_to > now())"):
        feeds[r["dst"]].append(r["src"])
        nodes |= {r["src"], r["dst"]}
    return feeds, nodes


def _units(conn, subjects: list[str], nodes: set[str]) -> list[str]:
    """The flow units a job takes down: its subjects on the flow network, or the ones that share a subject's parent."""
    on = [s for s in subjects if s in nodes]
    if on or not subjects:
        return on
    rows = conn.execute(
        """
        select distinct sib.src as id from edge p join edge sib on sib.dst = p.dst and sib.rel = 'part_of'
        where p.rel = 'part_of' and p.src = any(%s) and sib.src = any(%s)
        """, (subjects, list(nodes))).fetchall()
    return sorted(r["id"] for r in rows)


def _upstream(units: list[str], feeds: dict[str, list[str]]) -> set[str]:
    seen, stack = set(units), list(units)
    while stack:
        for src in feeds.get(stack.pop(), ()):
            if src not in seen:
                seen.add(src)
                stack.append(src)
    return seen


def _rates(conn, wells: list[str]) -> dict[str, dict[str, dict]]:
    """Each well's latest test, per rate: {well: {quantity: {value, unit, at}}}."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in conn.execute(
            """
            select distinct on (fuzer_id, quantity) fuzer_id, quantity, value, unit, at from measurement
            where fuzer_id = any(%s) and method = 'well test' and quantity = any(%s)
            order by fuzer_id, quantity, at desc
            """, (wells, list(RATES))):
        out[r["fuzer_id"]][r["quantity"]] = {"value": r["value"], "unit": r["unit"], "at": r["at"].date().isoformat()}
    return out


def _labels(conn, ids) -> dict[str, str]:
    return {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),))}


def _union_hours(spans: list[tuple[datetime, datetime]]) -> float:
    total, end = 0.0, None
    for a, b in sorted(spans):
        if end is None or a > end:
            total += (b - a).total_seconds()
            end = b
        elif b > end:
            total += (b - end).total_seconds()
            end = b
    return total / 3600


def windows(conn, start=None, days: int = HORIZON_DAYS) -> dict:
    """Shutdown windows over the horizon, each with its items, the wells behind them and the production at risk."""
    t0 = _when(start) if start else datetime.now(UTC)
    t1 = t0 + timedelta(days=days)
    rows = conn.execute(
        """
        select id, title, kind, source, subjects, start_at, end_at from plan_item
        where status = any(%(active)s) and (needs_shutdown or kind = any(%(sd)s))
          and start_at < %(t1)s and end_at > %(t0)s
        order by start_at, id
        """, {"active": list(ACTIVE), "sd": list(SHUTDOWNS), "t0": t0, "t1": t1}).fetchall()
    feeds, nodes = _flow(conn)
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where fuzer_id = any(%s)", (list(nodes),))} if nodes else {}
    items = []
    for r in rows:
        units = _units(conn, list(r["subjects"] or []), nodes)
        wells = sorted(n for n in _upstream(units, feeds) if kinds.get(n) == "well")
        a, b = max(r["start_at"], t0), min(r["end_at"], t1)
        items.append({"id": r["id"], "title": r["title"], "kind": r["kind"], "start": a, "end": b,
                      "hours": round((b - a).total_seconds() / 3600, 1), "assumed": ASSUMED.get(r["source"] or ""),
                      "units": units, "wells": wells})
    groups: list[list[dict]] = []
    for it in items:  # overlapping in time: one window
        if groups and it["start"] < max(x["end"] for x in groups[-1]):
            groups[-1].append(it)
        else:
            groups.append([it])
    every_well = sorted({w for it in items for w in it["wells"]})
    rates = _rates(conn, every_well) if every_well else {}
    labels = _labels(conn, every_well + [u for it in items for u in it["units"]])
    out = []
    for g in groups:
        spans: dict[str, list] = defaultdict(list)
        for it in g:
            for w in it["wells"]:
                spans[w].append((it["start"], it["end"]))
        wells, totals = [], defaultdict(lambda: {"value": 0.0, "unit": None})
        for w, s in sorted(spans.items(), key=lambda x: labels.get(x[0]) or x[0]):
            hours = _union_hours(s)
            wells.append({"id": w, "label": labels.get(w) or w, "hours": round(hours, 1), "rates": rates.get(w, {})})
            for q, rate in rates.get(w, {}).items():
                totals[q]["value"] += rate["value"] * hours / 24
                totals[q]["unit"] = (rate["unit"] or "").removesuffix("/d") or None
        out.append({
            "start": min(x["start"] for x in g).isoformat(), "end": max(x["end"] for x in g).isoformat(),
            "items": [{**{k: it[k] for k in ("id", "title", "kind", "hours", "assumed")},
                       "start": it["start"].isoformat(), "end": it["end"].isoformat(),
                       "units": [{"id": u, "label": labels.get(u) or u} for u in it["units"]], "wells": len(it["wells"])}
                      for it in g],
            "wells": wells, "untested": [w["label"] for w in wells if not w["rates"]],
            "at_risk": [{"quantity": q, "value": round(v["value"], 1), "unit": v["unit"]} for q, v in sorted(totals.items())],
            "assumptions": sorted({it["assumed"] for it in g if it["assumed"]}),
        })
    return {"start": t0.isoformat(), "end": t1.isoformat(), "days": days, "windows": out,
            "basis": "Wells behind each unit on the flow network, at their latest test rate, for the time the unit is "
                     "down; a well is counted once however many overlapping jobs take it down."}


TRIP_HOURS = 24


def trip(conn, item: str, hours: float = TRIP_HOURS) -> dict:
    """"If this trips" (P5.16d): what goes down with `item` and the production at risk over `hours`, from the same
    reads as the windows above. The units it takes down (itself on the flow network, or the flow units that share its
    parent), everything behind them (it has nowhere to flow), the wells among those at their latest test rate, and what
    is downstream with nothing else feeding it. An estimate, not a hydraulic model: no rerouting, no capacity, no
    restart time."""
    feeds, nodes = _flow(conn)
    units = _units(conn, [item], nodes)
    behind = _upstream(units, feeds)
    feeds_of = defaultdict(list)
    for dst, srcs in feeds.items():
        for s in srcs:
            feeds_of[s].append(dst)
    starved, grew = set(), True  # downstream, every feed down
    while grew:
        grew = False
        for n in {d for s in behind | starved for d in feeds_of.get(s, ())} - behind - starved:
            if all(f in behind or f in starved for f in feeds.get(n, ())):
                starved.add(n)
                grew = True
    every = behind | starved
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where fuzer_id = any(%s)", (list(every),))} if every else {}
    wells = sorted(n for n in behind if kinds.get(n) == "well")
    rates = _rates(conn, wells) if wells else {}
    labels = _labels(conn, every | {item})
    totals: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "unit": None})
    for w in wells:
        for q, rate in rates.get(w, {}).items():
            totals[q]["value"] += rate["value"] * hours / 24
            totals[q]["unit"] = (rate["unit"] or "").removesuffix("/d") or None

    def named(ids) -> list[dict]:
        return sorted(({"id": n, "label": labels.get(n) or n} for n in ids), key=lambda x: (x["label"], x["id"]))

    return {
        "item": {"id": item, "label": labels.get(item) or item}, "hours": hours,
        "units": named(units), "behind": named(n for n in behind - set(units) if kinds.get(n) != "well"),
        "starved": named(starved),
        "wells": [{**w, "rates": rates.get(w["id"], {})} for w in named(wells)],
        "untested": [labels.get(w) or w for w in wells if not rates.get(w)],
        "at_risk": [{"quantity": q, "value": round(v["value"], 1), "unit": v["unit"]} for q, v in sorted(totals.items())],
        "on_network": bool(units),
        "basis": "An estimate: the wells behind it on the flow network at their latest test rate, for the window. "
                 "It assumes nothing is rerouted and claims no hydraulics.",
    }

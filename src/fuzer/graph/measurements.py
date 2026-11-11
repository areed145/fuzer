"""Measurements: sparse, manual readings that aren't in a historian (well tests, lab samples,
inspection wall thicknesses at CMLs). Mappings with a `measurements` block say how to read them; the
rebuild lands them in the measurement table against the entity they were taken on, and the entity
page, the troubleshooting brief and the assistant read them from there next to PI values.
"""

from collections import Counter
from datetime import datetime

from ..core import db

COLS = ["record", "quantity", "fuzer_id", "at", "value", "unit", "qualifier", "reported", "method", "location"]


def build(conn, pack, maps: dict, endpoint, derive=None, extra=()) -> dict:
    """Read every measurement dataset into the measurement table (`endpoint` resolves what each was
    taken on, as graph.resolver). `derive(conn, pack, rows)` adds rows computed from them, landing in
    the same sync so a rebuild doesn't remove and rewrite them. `extra`: Measurements read elsewhere (the P&IDs'
    equipment data tables)."""
    rows, unresolved = {}, Counter()
    for x in extra:
        fid = next((f for f, _ in map(endpoint, x.of) if f), None)
        if fid is None:
            unresolved[f"{x.record.split(':')[0]}: {endpoint(x.of[0])[1]}"] += 1
            continue
        rows[(x.record, x.quantity)] = (x.record, x.quantity, fid, x.at, x.value, x.unit, x.qualifier, x.reported,
                                        x.method, x.location)
    for m in maps.values():
        if not m.measures:
            continue
        for rec in conn.execute("select source_key, payload from source_record where source = %s and record_type = %s",
                                (m.source, m.record_type)):
            for x in m.measure(rec["source_key"], rec["payload"], pack):
                fid = next((f for f, _ in map(endpoint, x.of) if f), None)
                if fid is None:
                    unresolved[f"{m.source} {m.record_type}: {endpoint(x.of[0])[1]}"] += 1
                    continue
                rows[(x.record, x.quantity)] = (x.record, x.quantity, fid, x.at, x.value, x.unit, x.qualifier,
                                                x.reported, x.method, x.location)
    if derive:
        rows |= derive(conn, pack, rows)
    sync = db.sync_table(conn, "measurement", COLS[:2], COLS[2:], rows.values())
    return {"measurements": len(rows), "unresolved_measurements": dict(unresolved),
            "measurement_changes": {k: v for k, v in sync.items() if k != "rows"}}


def _scope(conn, fuzer_id: str) -> list[str]:
    """The entity, what it's about (an incident's unit), and what is installed at it or it is installed
    at, so a tag shows its equipment's readings and the other way round."""
    ids = {fuzer_id}
    ids |= {r["dst"] for r in conn.execute(
        "select dst from edge where src = %s and rel = 'about'", (fuzer_id,))}
    ids |= {r["other"] for r in conn.execute(
        """
        select case when src = any(%(ids)s) then dst else src end as other from edge
        where rel = 'installed_at' and valid_to is null and (src = any(%(ids)s) or dst = any(%(ids)s))
        """, {"ids": list(ids)})}
    return sorted(ids)


def for_entity(conn, fuzer_id: str, quantity: str | None = None, since: str | None = None,
               until: str | None = None, limit: int = 5000) -> dict:
    """Readings on an entity (see _scope), as one series per quantity and location (each CML is its
    own series), oldest first, with the latest value and the change per year."""
    rows = conn.execute(
        """
        select m.*, l.label from measurement m left join entity_label l using (fuzer_id)
        where m.fuzer_id = any(%s) and (%s::text is null or m.quantity = %s)
          and (%s::timestamp is null or m.at >= %s::timestamp) and (%s::timestamp is null or m.at < %s::timestamp)
        order by m.at desc limit %s
        """,
        (_scope(conn, fuzer_id), quantity, quantity, since, since, until, until, limit),
    ).fetchall()
    groups: dict[tuple, list[dict]] = {}
    for r in reversed(rows):
        groups.setdefault((r["quantity"], r["location"], r["fuzer_id"]), []).append(r)
    series = []
    for (q, loc, fid), rs in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1] or "", g[0][2])):
        last = rs[-1]
        series.append({
            "quantity": q, "location": loc, "of": {"id": fid, "label": last["label"]}, "unit": last["unit"],
            "count": len(rs), "first": rs[0]["at"].isoformat(), "min": min(r["value"] for r in rs),
            "max": max(r["value"] for r in rs),
            "latest": {"at": last["at"].isoformat(), "value": last["value"], "qualifier": last["qualifier"],
                       "method": last["method"], "record": last["record"]},
            "per_year": per_year([(r["at"], r["value"]) for r in rs]),
            "values": [{"at": r["at"].isoformat(), "value": r["value"], "qualifier": r["qualifier"],
                        "reported": r["reported"], "method": r["method"], "record": r["record"]} for r in rs],
        })
    return {"id": fuzer_id, "series": series, "truncated": len(rows) == limit}


def per_year(points: list[tuple[datetime, float]]) -> float | None:
    """Least-squares change per year (a CML's wall loss, a well's decline); None for under two readings
    or under a month apart."""
    if len(points) < 2 or (points[-1][0] - points[0][0]).days < 30:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / (365.25 * 86400) for t, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var, 4) if var else None


def summary(conn, fuzer_id: str, recent: int = 3, limit: int = 12) -> list[dict]:
    """Per series: the latest readings and the change per year, without the full history (for the
    troubleshooting brief and agents)."""
    out = for_entity(conn, fuzer_id)["series"]
    out.sort(key=lambda s: s["latest"]["at"], reverse=True)
    return [{k: v for k, v in s.items() if k != "values"} | {"recent": s["values"][-recent:]} for s in out[:limit]]

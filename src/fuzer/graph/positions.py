"""Positions along a line (#61): pipelines locate things by station, metres from the line's start (PODS,
in-line inspection), not by map coordinates.

- stationing(): the station of each vertex of a line's GIS route. A route is drawn simpler than the pipe is laid,
  so map distance is scaled to the line's declared length where it has one: station 0 is the route's first point
  and the line's length its last.
- at() / between(): a point at a station, or the stretch of route between two, by those stations.
- build(): with the register rebuilt, each pipeline feature found on a line is linked to the segment whose station
  range holds it (`found_on`, its station in the link's props), and features and segments go on the map along their
  line's route. A feature whose station no segment holds stays on its line, and the stats count it.
"""

from itertools import pairwise

from psycopg.types.json import Jsonb

from .spatial import _coords, metres


def stationing(route: dict, length_m: float | None = None) -> list[tuple[float, float, float]]:
    """(station_m, lon, lat) for each vertex of a LineString route, scaled to `length_m` when given."""
    pts = [(float(p[0]), float(p[1])) for p in _coords(route or {})]
    if not pts:
        return []
    run = [0.0]
    for a, b in pairwise(pts):
        run.append(run[-1] + metres(a, b))
    k = length_m / run[-1] if length_m and run[-1] else 1.0
    return [(s * k, x, y) for s, (x, y) in zip(run, pts)]


def at(stations: list[tuple[float, float, float]], station_m: float) -> tuple[float, float] | None:
    """The point at a station (lon, lat), interpolated along the route; None off either end of it."""
    if not stations or station_m < 0 or station_m > stations[-1][0] + 1e-6:
        return None
    for (s0, x0, y0), (s1, x1, y1) in pairwise(stations):
        if station_m <= s1:
            f = (station_m - s0) / (s1 - s0) if s1 > s0 else 0.0
            return (round(x0 + f * (x1 - x0), 6), round(y0 + f * (y1 - y0), 6))
    return stations[-1][1], stations[-1][2]


def between(stations: list[tuple[float, float, float]], a: float, b: float) -> list[tuple[float, float]]:
    """The route from station a to station b, its ends interpolated."""
    start, end = at(stations, a), at(stations, b)
    if start is None or end is None:
        return []
    return [start, *[(x, y) for s, x, y in stations if a < s < b], end]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(conn) -> dict:
    """Features to their segments, and features and segments on the map along their line's route."""
    lines = {}
    for r in conn.execute(
        """
        select e.fuzer_id, gi.geometry, l.value #>> '{}' as length_km
        from entity e join geo_index gi using (fuzer_id)
        left join golden l on l.fuzer_id = e.fuzer_id and l.field = 'length_km'
        where e.kind = 'pipeline' and gi.geom_type = 'LineString'
        """):
        km = _num(r["length_km"])
        lines[r["fuzer_id"]] = stationing(r["geometry"], km * 1000 if km else None)

    def stations(fid: str, *fields: str) -> list:
        return [_num(r["v"]) for f in fields for r in conn.execute(
            "select value #>> '{}' as v from golden where fuzer_id = %s and field = %s", (fid, f))] or [None]

    segments: dict[str, list[tuple[float, float, str]]] = {}
    geo = []
    for r in conn.execute(
        "select e.src, e.dst from edge e join entity s on s.fuzer_id = e.src and s.kind = 'pipeline_segment' "
        "join entity p on p.fuzer_id = e.dst and p.kind = 'pipeline' where e.rel = 'part_of' and e.valid_to is null"):
        a, b = (stations(r["src"], f)[0] for f in ("station_from_m", "station_to_m"))
        if a is None or b is None or b <= a:
            continue
        segments.setdefault(r["dst"], []).append((a, b, r["src"]))
        if route := between(lines.get(r["dst"], []), a, b):
            geo.append((r["src"], "pipeline_segment", {"type": "LineString", "coordinates": [list(p) for p in route]}))

    links, placed, unheld = [], 0, 0
    for r in conn.execute(
        "select e.src, e.dst from edge e join entity f on f.fuzer_id = e.src and f.kind = 'pipeline_feature' "
        "join entity p on p.fuzer_id = e.dst and p.kind = 'pipeline' where e.rel = 'found_on' and e.valid_to is null"):
        s = stations(r["src"], "station_m")[0]
        if s is None:
            continue
        # a station on a boundary belongs to the segment it starts
        mine = segments.get(r["dst"], [])
        end = max((b for _, b, _ in mine), default=None)
        held = [seg for a, b, seg in mine if a <= s < b or s == b == end]
        if held:
            links.append((r["src"], "found_on", held[0], ["positions"], 1.0,
                          Jsonb({"station_m": s, "line": r["dst"], "method": "station"})))
        else:
            unheld += 1
        if p := at(lines.get(r["dst"], []), s):
            geo.append((r["src"], "pipeline_feature", {"type": "Point", "coordinates": list(p)}))
            placed += 1
    with conn.cursor() as cur:
        cur.execute("delete from edge where 'positions' = any(sources)")
        cur.executemany(
            "insert into edge (src, rel, dst, sources, confidence, props) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (src, rel, dst, valid_from) do update set props = excluded.props", links)
    rows = []
    for fid, kind, g in geo:
        pts = _coords(g)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        rows.append((fid, kind, g["type"], min(xs), min(ys), max(xs), max(ys), sum(xs) / len(xs), sum(ys) / len(ys),
                     Jsonb(g)))
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into geo_index (fuzer_id, kind, geom_type, minx, miny, maxx, maxy, cx, cy, geometry)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (fuzer_id) do update set kind = excluded.kind, geom_type = excluded.geom_type,
                    minx = excluded.minx, miny = excluded.miny, maxx = excluded.maxx, maxy = excluded.maxy,
                    cx = excluded.cx, cy = excluded.cy, geometry = excluded.geometry
                """, rows)
    return {"segments": sum(map(len, segments.values())), "features_on_segments": len(links),
            "features_placed": placed, "features_unheld": unheld}


def along(conn, fuzer_id: str) -> dict:
    """What is along a line or segment, in station order: its segments and the features found on it."""
    kind = conn.execute("select kind from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if not kind or kind["kind"] not in ("pipeline", "pipeline_segment"):
        return {"segments": [], "features": []}
    line = fuzer_id
    if kind["kind"] == "pipeline_segment":
        up = conn.execute("select dst from edge where src = %s and rel = 'part_of' and valid_to is null",
                          (fuzer_id,)).fetchone()
        line = up["dst"] if up else None

    def golden(ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {i: {} for i in ids}
        for r in conn.execute("select fuzer_id, field, value from golden where fuzer_id = any(%s)", (ids,)):
            out[r["fuzer_id"]][r["field"]] = r["value"]
        return out

    seg_ids = [r["src"] for r in conn.execute(
        "select src from edge where dst = %s and rel = 'part_of' and valid_to is null "
        "and src in (select fuzer_id from entity where kind = 'pipeline_segment')", (line,))]
    segs = golden(seg_ids)
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (seg_ids,))}
    segments = sorted(({"id": i, "label": labels.get(i, i), **{k: segs[i].get(k) for k in (
        "description", "station_from_m", "station_to_m", "wall_thickness_mm", "grade", "class_location")}}
        for i in seg_ids), key=lambda s: _num(s["station_from_m"]) or 0)
    on = fuzer_id if kind["kind"] == "pipeline_segment" else line
    feats = conn.execute(
        """
        select e.src, e.props, l.label from edge e left join entity_label l on l.fuzer_id = e.src
        where e.dst = %s and e.rel = 'found_on' and e.valid_to is null
        """, (on,)).fetchall()
    fg = golden([f["src"] for f in feats])
    features = sorted(({"id": f["src"], "label": f["label"] or f["src"], **{k: fg[f["src"]].get(k) for k in (
        "feature_type", "station_m", "depth_pct", "length_mm", "clock", "run_id", "run_date")},
        "segment": next((s["id"] for s in segments if (_num(s["station_from_m"]) or 0) <= (_num(fg[f["src"]].get(
            "station_m")) or -1) < (_num(s["station_to_m"]) or 0)), None)} for f in feats),
        key=lambda f: _num(f["station_m"]) or 0)
    return {"line": line, "segments": segments, "features": features}


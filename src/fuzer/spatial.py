"""Where things are: a geometry index, spatial linking, and map features.

- index(): every entity with a `geometry` (GeoJSON, from GIS) or latitude / longitude goes into
  geo_index with its bounding box and centre, rebuilt with the register. Map and nearby queries
  are bounding-box lookups on it; no PostGIS needed for points and lines at asset scale.
- link(): the asset pack's `spatial_links` rules add links GIS data didn't carry as IDs, scored by
  distance: a well location to the pad it sits on, a pipeline end to the facility it reaches.
- features() / around(): GeoJSON for the map page and an entity's map.
"""

import math

from psycopg.types.json import Jsonb

from . import db

EARTH_M = 6_371_000
# Properties shown on the map, from golden values.
MAP_FIELDS = ("description", "service", "diameter_in", "length_km", "mop_barg", "status", "facility_type",
              "gis_name", "install_year", "water_depth_m")


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def _coords(geom: dict) -> list[list[float]]:
    c, t = geom.get("coordinates"), geom.get("type")
    if t == "Point":
        return [c]
    if t in ("LineString", "MultiPoint"):
        return list(c)
    if t in ("Polygon", "MultiLineString"):
        return [p for ring in c for p in ring]
    if t == "MultiPolygon":
        return [p for poly in c for ring in poly for p in ring]
    return []


def index(conn) -> int:
    """Rebuild geo_index from golden geometry (or latitude / longitude)."""
    rows = []
    for r in conn.execute(
        """
        select e.fuzer_id, e.kind, g.value as geom, la.value #>> '{}' as lat, lo.value #>> '{}' as lon
        from entity e
        left join golden g on g.fuzer_id = e.fuzer_id and g.field = 'geometry'
        left join golden la on la.fuzer_id = e.fuzer_id and la.field = 'latitude'
        left join golden lo on lo.fuzer_id = e.fuzer_id and lo.field = 'longitude'
        where e.retired_at is null and e.merged_into is null and (g.value is not null or la.value is not null)
        """
    ):
        geom = r["geom"] if isinstance(r["geom"], dict) else None
        if not geom and r["lat"] and r["lon"]:
            geom = {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]}
        pts = _coords(geom or {})
        if not pts:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        rows.append((r["fuzer_id"], r["kind"], geom["type"], min(xs), min(ys), max(xs), max(ys),
                     sum(xs) / len(xs), sum(ys) / len(ys), Jsonb(geom)))
    db.sync_table(conn, "geo_index", ["fuzer_id"],
                  ["kind", "geom_type", "minx", "miny", "maxx", "maxy", "cx", "cy", "geometry"], rows)
    return len(rows)


class _Grid:
    """Points in ~1 km cells, for nearest-within-distance lookups without scanning everything."""

    CELL = 0.01  # degrees

    def __init__(self, points: list[tuple[str, float, float]]):
        self.cells: dict[tuple[int, int], list[tuple[str, float, float]]] = {}
        for p in points:
            self.cells.setdefault((int(p[1] // self.CELL), int(p[2] // self.CELL)), []).append(p)

    def nearest(self, x: float, y: float, max_m: float, skip: str | None = None) -> tuple[str, float] | None:
        reach = int(max_m / 1000 / (self.CELL * 111)) + 1
        cx, cy = int(x // self.CELL), int(y // self.CELL)
        best = None
        for i in range(cx - reach, cx + reach + 1):
            for j in range(cy - reach, cy + reach + 1):
                for fid, px, py in self.cells.get((i, j), ()):
                    if fid == skip:
                        continue
                    d = metres((x, y), (px, py))
                    if d <= max_m and (best is None or d < best[1]):
                        best = (fid, d)
        return best


def link(conn, rules: list[dict]) -> dict:
    """Links from the pack's spatial rules, written as edges (source 'spatial', confidence by distance).

    A rule: {kind, to: [kinds], max_m, rel} links each point of `kind` to the nearest `to` within
    max_m; with `ends: [start_rel, end_rel]`, each end of a line links to the nearest `to` instead.
    `only_missing: true` skips a rel the entity already has (GIS that did carry the ID wins)."""
    stats = {}
    for rule in rules:
        targets = _Grid([(r["fuzer_id"], r["cx"], r["cy"]) for r in conn.execute(
            "select fuzer_id, cx, cy from geo_index where kind = any(%s)", (rule["to"],))])
        have = {(r["src"], r["rel"]) for r in conn.execute(
            "select src, rel from edge where src in (select fuzer_id from geo_index where kind = %s)", (rule["kind"],))}
        rows = []
        for r in conn.execute("select fuzer_id, geometry from geo_index where kind = %s", (rule["kind"],)):
            pts = _coords(r["geometry"])
            places = list(zip(rule["ends"], (pts[0], pts[-1]))) if rule.get("ends") else [(rule["rel"], (
                sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)))]
            for rel, (x, y) in places:
                if rule.get("only_missing") and (r["fuzer_id"], rel) in have:
                    continue
                hit = targets.nearest(x, y, rule["max_m"], skip=r["fuzer_id"])
                if hit:
                    dst, d = hit
                    rows.append((r["fuzer_id"], rel, dst, ["spatial"], round(1 - 0.5 * d / rule["max_m"], 3),
                                 Jsonb({"distance_m": round(d, 1), "method": "spatial", "max_m": rule["max_m"]})))
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into edge (src, rel, dst, sources, confidence, props) values (%s, %s, %s, %s, %s, %s)
                on conflict (src, rel, dst, valid_from) do nothing
                """, rows)
        stats[f"{rule['kind']}->{'/'.join(rule['to'])}"] = len(rows)
    return stats


def _features(conn, where: str, params: dict, limit: int) -> dict:
    rows = conn.execute(
        f"""
        select gi.fuzer_id, gi.kind, gi.geometry, l.label,
               (select jsonb_object_agg(g.field, g.value) from golden g
                 where g.fuzer_id = gi.fuzer_id and g.field = any(%(fields)s)) as props
        from geo_index gi left join entity_label l using (fuzer_id)
        where {where}
        order by gi.geom_type <> 'Point', gi.fuzer_id
        limit %(limit)s
        """, {**params, "fields": list(MAP_FIELDS), "limit": limit}).fetchall()
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": r["fuzer_id"], "geometry": r["geometry"],
         "properties": {"id": r["fuzer_id"], "kind": r["kind"], "label": r["label"], **(r["props"] or {})}}
        for r in rows], "truncated": len(rows) == limit}


def features(conn, bbox: tuple[float, float, float, float] | None = None, kinds: list[str] | None = None,
             limit: int = 20000) -> dict:
    """Map features (GeoJSON), optionally within a bounding box (min lon, min lat, max lon, max lat)."""
    where = ["true"]
    params: dict = {}
    if bbox:
        where.append("gi.maxx >= %(x0)s and gi.minx <= %(x1)s and gi.maxy >= %(y0)s and gi.miny <= %(y1)s")
        params |= dict(zip(("x0", "y0", "x1", "y1"), bbox))
    if kinds:
        where.append("gi.kind = any(%(kinds)s)")
        params["kinds"] = kinds
    return _features(conn, " and ".join(where), params, limit)


def around(conn, fuzer_id: str, limit: int = 500) -> dict:
    """An entity's map: its own geometry, or where it sits (the nearest place up its part_of chain),
    plus things with geometry linked to that place (pipelines that start or end there, wells on it)."""
    place = conn.execute(
        """
        with recursive up(id, depth) as (
            select %(id)s::text, 0
            union
            select e.dst, up.depth + 1 from up join edge e on e.src = up.id and e.rel in ('part_of', 'installed_at',
                                                                                          'located_at', 'about')
            where up.depth < 4
        )
        select up.id from up join geo_index gi on gi.fuzer_id = up.id order by up.depth limit 1
        """, {"id": fuzer_id}).fetchone()
    if not place:
        return {"type": "FeatureCollection", "features": [], "focus": None}
    out = _features(conn, """
        gi.fuzer_id = %(p)s
        or gi.fuzer_id in (select src from edge where dst = %(p)s)
        or gi.fuzer_id in (select dst from edge where src = %(p)s)
        or gi.fuzer_id in (select e2.dst from edge e1 join edge e2 on e2.src = e1.src
                           where e1.dst = %(p)s and e1.rel in ('starts_at', 'ends_at') and e2.rel in ('starts_at', 'ends_at'))
        """, {"p": place["id"]}, limit)
    out["focus"] = place["id"]
    return out

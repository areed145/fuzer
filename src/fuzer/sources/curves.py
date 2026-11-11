"""Reference curves per equipment model (#92): OEM pump curves and choke characteristics; and per machine (#474):
compressor maps, which a vendor issues for one machine's wheels.

They arrive as vendor CSV exports (a mapping with a `curves` block) or on datasheets, which Claude
reads (llm_curves.py) into review items; accepted ones are kept in curve_digitised. The rebuild lands
both in equipment_curve against the equipment_model entity, so every pump or choke of that model gets
the curve through instance_of. Charts (plot_pump_curve, plot_choke) read them from here.
"""

import itertools
from collections import Counter

from ..core import db

COLS = ["model_id", "source", "series", "x", "curve", "y", "x_unit", "y_unit", "record"]


def build(conn, pack, maps: dict, endpoint) -> dict:
    """Vendor exports and accepted datasheet readings into equipment_curve."""
    rows, unresolved = {}, Counter()
    for m in maps.values():
        if not m.curve_spec:
            continue
        for rec in conn.execute("select source_key, payload from source_record where source = %s and record_type = %s",
                                (m.source, m.record_type)):
            for p in m.curve_points(rec["source_key"], rec["payload"], pack):
                fid, why = endpoint(p.of)
                if fid is None:
                    unresolved[f"{m.source} {m.record_type}: {why}"] += 1
                    continue
                rows[(fid, m.source, p.series, p.x)] = (fid, m.source, p.series, p.x, p.curve, p.y, p.x_unit, p.y_unit,
                                                         p.record)
    for d in conn.execute("select * from curve_digitised"):
        fid, why = endpoint(("equipment_model", d["model_key"]))
        if fid is None:
            unresolved[f"datasheet {d['doc_number']}: {why}"] += 1
            continue
        r, src = d["result"], f"datasheet:{d['doc_number']}"
        units = r.get("units", {})
        for name, pts in r.get("series", {}).items():
            for x, y in pts:
                rows[(fid, src, name, float(x))] = (fid, src, name, float(x), r.get("curve"), y, units.get("x"),
                                                    units.get(name), d["document_key"])
        for name, xy in r.get("ratings", {}).items():
            x, y = (xy + [None])[:2] if isinstance(xy, list) else (xy, None)
            if x is not None:
                rows[(fid, src, name, float(x))] = (fid, src, name, float(x), r.get("curve"), y, None, None, d["document_key"])
    sync = db.sync_table(conn, "equipment_curve", COLS[:4], COLS[4:], rows.values())
    return {"curve_points": len(rows), "unresolved_curve_points": dict(unresolved),
            "curve_changes": {k: v for k, v in sync.items() if k != "rows"}}


def model_of(conn, fid: str) -> str | None:
    """The equipment model behind a model, an equipment item, or a tag (what is installed there now)."""
    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind is None:
        return None
    if kind["kind"] == "equipment_model":
        return fid
    if kind["kind"] == "tag":
        eq = conn.execute("select src from edge where rel = 'installed_at' and dst = %s and valid_to is null "
                          "order by valid_from desc nulls last limit 1", (fid,)).fetchone()
        fid = eq["src"] if eq else fid
    m = conn.execute("select dst from edge where rel = 'instance_of' and src = %s limit 1", (fid,)).fetchone()
    return m["dst"] if m else None


def for_model(conn, model_id: str) -> dict | None:
    """The model's curves: the vendor export when there is one, else an accepted datasheet reading."""
    rows = conn.execute("select * from equipment_curve where model_id = %s order by source, series, x",
                        (model_id,)).fetchall()
    if not rows:
        return None
    sources = sorted({r["source"] for r in rows}, key=lambda s: (s != "oem", s))
    src = sources[0]
    out = {"model_id": model_id, "source": src, "other_sources": sources[1:], "curve": None, "series": {}, "ratings": {},
           "units": {}}
    for r in rows:
        if r["source"] != src:
            continue
        out["curve"] = out["curve"] or r["curve"]
        if r["series"] in ("head", "efficiency", "npshr", "power", "cv", "surge", "power_ambient", "heat_rate_ambient",
                           "heat_rate_load") or "@" in r["series"]:
            out["series"].setdefault(r["series"], []).append([r["x"], r["y"]])
            out["units"][r["series"]] = r["y_unit"]
            out["units"]["x"] = r["x_unit"] or out["units"].get("x")
        else:
            out["ratings"][r["series"]] = [r["x"], r["y"]]
    return out


def for_machine(conn, fid: str) -> dict | None:
    """The curves of one machine (a compressor map is issued per machine, #474): the item installed at a tag, or the
    item itself; None when it has none of its own."""
    kind = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if kind is None:
        return None
    if kind["kind"] == "tag":
        eq = conn.execute("select src from edge where rel = 'installed_at' and dst = %s and valid_to is null "
                          "order by valid_from desc nulls last limit 1", (fid,)).fetchone()
        if not eq:
            return None
        fid = eq["src"]
    return for_model(conn, fid)


def at(points: list[list[float]], x: float) -> float | None:
    """A curve's value at x, interpolated (None outside it)."""
    pts = sorted(points)
    for (x0, y0), (x1, y1) in itertools.pairwise(pts):
        if x0 <= x <= x1 and x1 > x0:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def waiting(conn, model_id: str) -> list[dict]:
    """Datasheets for a model with no accepted reading yet (so a chart can say why it has no curve)."""
    return conn.execute(
        """
        select d.src as document, l.label, r.status, r.id as review_item
        from edge d join entity_label l on l.fuzer_id = d.src
        join golden g on g.fuzer_id = d.src and g.field = 'doc_type' and g.value #>> '{}' = 'datasheet'
        left join review_item r on r.kind = 'curve' and r.payload->>'document' = d.src
        where d.rel = 'applies_to' and d.dst = %s
        """, (model_id,)).fetchall()

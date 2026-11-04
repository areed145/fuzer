"""Sources before they load (#132): every table in a data folder with its connector, mapping and state, and
a preview of one: sample rows, schema drift against the last load, and what the rows would become (the
entities and links a mapped table makes, or the draft mapping rules suggest for a new one). Reads only.

    fuzer source list
    fuzer source preview instrument_index
"""

from pathlib import Path

from ..core.jsonsafe import plain
from . import catalog, connectors, ingest, suggest


def _held(conn) -> set:
    return {r["subject"] for r in conn.execute("select subject from review_item where kind = 'schema' and status = 'open'")}


def listing(conn, data_dir) -> list[dict]:
    """Each catalog dataset found through its connection, and each table in the data folder no dataset covers:
    system, connector, mapping (source/record type), status, load mode, state and the last load."""
    data_dir = Path(data_dir)
    catalog.ensure(conn)
    ds, tables, held = catalog.datasets(conn), ingest.tables(conn), _held(conn)
    out = []
    for name in sorted(set(ds) | set(ingest.unmapped(data_dir, conn))):
        d = ds.get(name)
        c = ingest.dataset(data_dir, name, conn) if d else connectors.find(data_dir, name)
        if c is None:
            continue
        m = tables.get(name)
        if name in held:
            state = "held"  # its columns changed: waiting for review
        elif d is None:
            state = "unmapped"
        elif d["status"] != "active":
            state = d["status"]
        elif d["fingerprint"] is None:
            state = "new"
        else:
            state = "unchanged" if d["fingerprint"] == c.fingerprint() else "changed"
        out.append({"dataset": name, "system": d["source"] if d else None, "connector": c.kind,
                    "mapping": f"{m.source}/{m.record_type}" if m else ("built-in loader" if d and d["reader"] else None),
                    "status": d["status"] if d else None, "load_mode": d["load_mode"] if d else None,
                    "state": state, "rows": d["rows"] if d else None,
                    "loaded_at": d["loaded_at"].isoformat(timespec="seconds") if d and d["loaded_at"] else None})
    return out


def preview(conn, pack, data_dir, name: str, rows: int = 5) -> dict:
    """One table: a sample, its schema and drift since the last load, and what it would make."""
    c = ingest.dataset(Path(data_dir), name, conn) or connectors.find(Path(data_dir), name)
    if c is None:
        raise ValueError(f"no table {name!r} in {data_dir}")
    schema = c.schema()
    prev = catalog.dataset(conn, name) or {}
    out = {"dataset": name, "connector": c.kind, "path": str(getattr(c, "path", "")), "schema": schema,
           "drift": connectors.schema_drift(prev["schema"], schema) if prev.get("schema") and schema else None,
           "held": name in _held(conn)}
    if prev.get("reader"):  # nested manifests have their own loaders
        out["sample"] = [r[3] for _, r in zip(range(rows), c.records())]
        return plain(out)
    sample = [r for _, r in zip(range(rows), c.rows())]
    out["sample"] = sample
    m = ingest.tables(conn).get(name)
    if m is None:
        s = suggest.suggest_table(conn, pack, name, c)  # rules only: Claude is asked from suggest-mappings
        out["suggested"] = {"yaml": suggest.to_yaml(s["mapping"]), "key": s["suggestion"]["key"],
                            "columns": {k: v["meaning"] for k, v in s["suggestion"]["columns"].items()}}
        return plain(out)
    makes = []
    cols = m.key if isinstance(m.key, tuple) else (m.key,)
    for row in sample:
        key = ">".join(str(row[k]) for k in cols)
        cands, edges = m.extract(key, row, pack)
        makes.append({"record": key,
                      "entities": [{"kind": x.kind, "key": x.key, "fields": sorted(x.fields)} for x in cands],
                      "links": [{"rel": e.rel, "to": f"{e.dst[0]}:{e.dst[1]}"}  # a made entity carries its links
                                for e in [*edges, *(e for x in cands for e in x.edges)]]})
    out["mapping"] = f"{m.source}/{m.record_type}"
    out["makes"] = makes
    return plain(out)

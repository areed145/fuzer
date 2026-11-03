"""Sources before they load (#132): every table in a data folder with its connector, mapping and state, and
a preview of one: sample rows, schema drift against the last load, and what the rows would become (the
entities and links a mapped table makes, or the draft mapping rules suggest for a new one). Reads only.

    fuzer source list
    fuzer source preview instrument_index
"""

from pathlib import Path

from ..core.jsonsafe import plain
from . import connectors, ingest, suggest


def _state(conn) -> tuple[dict, set]:
    loaded = {r["dataset"]: r for r in conn.execute("select * from dataset_state")}
    held = {r["subject"] for r in conn.execute(
        "select subject from review_item where kind = 'schema' and status = 'open'")}
    return loaded, held


def listing(conn, data_dir) -> list[dict]:
    """Each table in the folder: connector, mapping (source/record type), state and the last load."""
    data_dir = Path(data_dir)
    tables = ingest.tables(conn)
    loaded, held = _state(conn)
    names = sorted(set(connectors.datasets(data_dir)) | {n for n in ingest.export_names(conn)
                                                         if ingest.dataset(data_dir, n, conn) is not None})
    out = []
    for name in names:
        c = ingest.dataset(data_dir, name, conn)
        if c is None:
            continue
        m, prev = tables.get(name), loaded.get(name)
        if name in held:
            state = "held"  # its columns changed: waiting for review
        elif name not in tables and name not in ingest.FILES:
            state = "unmapped"
        elif prev is None:
            state = "new"
        else:
            state = "unchanged" if prev["fingerprint"] == c.fingerprint() else "changed"
        out.append({"dataset": name, "connector": c.kind, "mapping": f"{m.source}/{m.record_type}" if m else
                    ("built-in loader" if name in ingest.FILES else None),
                    "state": state, "rows": prev["rows"] if prev else None,
                    "loaded_at": prev["loaded_at"].isoformat(timespec="seconds") if prev else None})
    return out


def preview(conn, pack, data_dir, name: str, rows: int = 5) -> dict:
    """One table: a sample, its schema and drift since the last load, and what it would make."""
    c = ingest.dataset(Path(data_dir), name, conn)
    if c is None:
        raise ValueError(f"no table {name!r} in {data_dir}")
    schema = c.schema()
    loaded, held = _state(conn)
    prev = loaded.get(name)
    out = {"dataset": name, "connector": c.kind, "path": str(getattr(c, "path", "")), "schema": schema,
           "drift": connectors.schema_drift(prev["schema"], schema) if prev and schema else None,
           "held": name in held}
    if name in ingest.FILES:  # nested manifests have their own loaders
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

"""Adding a source in one flow (#225, the Sources page's Add a source): any table a connection holds is previewed,
given a suggested mapping for its system, and saved as a dataset on that connection and loaded, in one step.

    tables(conn, connection_id)                 what the connection holds, and which dataset covers each
    preview(conn, pack, connection_id, table)   a sample, its columns, a format hint and a suggested mapping
    add(conn, pack, connection_id, table, ...)  the dataset, its approved mapping, and its first load
"""

import re
from pathlib import Path

import yaml

from ..core.jsonsafe import plain
from . import catalog, connections, connectors

_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})( \d{1,2}:\d{2})?$")
_MDY_OR_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})")


class OnboardError(ValueError):
    pass


def _connection(conn, connection_id: int) -> dict:
    c = catalog.connection(conn, connection_id)
    if not c:
        raise OnboardError(f"no connection {connection_id}")
    return c


def reader(conn, connection_id: int, table: str, data_dir):
    """The reader for a table on a connection, whether or not a dataset covers it yet."""
    c = _connection(conn, connection_id)
    r = connections.open_(c).connector({"name": table}, Path(data_dir))
    if r is None:
        raise OnboardError(f"{table!r} isn't on connection {c['name']}")
    return r


def _covered(conn, c: dict) -> dict[str, str]:
    """Table name -> the dataset that reads it, for datasets on this connection (and, for a folder with no path,
    on any other folder connection with no path: they read the same project data folder)."""
    default = c["type"] == "folder" and not (c["config"] or {}).get("path")
    out = {}
    for d in catalog.datasets(conn).values():
        same = d["connection_id"] == c["id"] or (
            default and d["connection_type"] in (None, "folder") and not (d["connection_config"] or {}).get("path"))
        if same:
            out[d["location"] or d["name"]] = d["name"]  # a reader's dataset has its reader's name
    return out


def tables(conn, connection_id: int, data_dir) -> dict:
    """What a connection holds: each table with the dataset that covers it (None: not loaded yet), new ones first."""
    c = _connection(conn, connection_id)
    try:
        found = connections.open_(c).discover(Path(data_dir))
    except connections.ConnectionError_ as e:
        raise OnboardError(str(e)) from e
    cov = _covered(conn, c)
    rows = [{"table": t, "dataset": cov.get(t)} for t in found]
    return {"connection": c["name"], "system": c["system"], "type": c["type"],
            "tables": sorted(rows, key=lambda r: (r["dataset"] is not None, r["table"])),
            "subfolders": _subfolders(c, Path(data_dir))}


def _subfolders(c: dict, data_dir: Path) -> list[dict]:
    """A folder connection reads only its own folder; subfolders holding tables of their own each need a connection
    (#412). Each with how many tables it holds and its path relative to the data folder where it is inside it."""
    if c["type"] != "folder":
        return []
    root = connections.open_(c).root(data_dir)
    if not root.is_dir() or connectors.is_table_folder(root) or connectors.is_document_folder(root):
        return []
    out = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        if connectors.is_table_folder(sub) or not (n := len(connectors.datasets(sub))):
            continue
        try:
            path = str(sub.relative_to(data_dir))
        except ValueError:
            path = str(sub)
        out.append({"name": sub.name, "path": path, "tables": n})
    return out


def date_hint(sample: list[dict]) -> dict | None:
    """A date format to set on the system when the sample's dates aren't ISO: day first when a first part is over
    12 (31/12/2025), month first when a second part is."""
    seen = [v for r in sample for v in r.values() if isinstance(v, str) and _MDY_OR_DMY.match(v.strip())]
    if not seen:
        return None
    sep = next(ch for ch in "/.-" if ch in seen[0])
    first = max(int(_MDY_OR_DMY.match(v.strip()).group(1)) for v in seen)
    second = max(int(_MDY_OR_DMY.match(v.strip()).group(2)) for v in seen)
    timed = any(":" in v for v in seen)
    order = "%d{s}%m{s}%Y" if first > 12 or second <= 12 else "%m{s}%d{s}%Y"
    fmt = order.format(s=sep) + (" %H:%M" if timed else "")
    return {"date_format": fmt, "example": seen[0], "certain": first > 12 or second > 12}


def _record_type(table: str, system: str) -> str:
    t = table.removeprefix(system + "_")
    return re.sub(r"s$", "", t) or "row"


def preview(conn, pack, connection_id: int, table: str, data_dir, rows: int = 8, claude: bool = False,
            dataset: str | None = None, record_type: str | None = None) -> dict:
    """A table on a connection: a sample, columns, a date-format hint for its system, and a mapping suggested for
    it (rules, or Claude for what rules can't name) with the system as its source."""
    from . import suggest

    c = _connection(conn, connection_id)
    r = reader(conn, connection_id, table, data_dir)
    dataset = dataset or table
    record_type = record_type or _record_type(table, c["system"])
    sample = [x for _, x in zip(range(rows), r.rows())] if hasattr(r, "rows") else []
    s = suggest.suggest_table(conn, pack, dataset, r, claude=claude, source=c["system"], record_type=record_type)
    m = {**s["mapping"], "source": c["system"], "record_type": record_type, "dataset": dataset}
    if dataset != table:
        m["location"] = table
    sys_row = catalog.system(conn, c["system"]) or {}
    hint = date_hint(sample)
    return plain({
        "connection": c["name"], "system": c["system"], "table": table, "connector": getattr(r, "kind", None),
        "path": str(getattr(r, "path", "")), "schema": r.schema(), "sample": sample,
        "covered_by": _covered(conn, c).get(table),
        "date_hint": hint if hint and sys_row.get("date_format") != hint["date_format"] else None,
        "suggested": {"yaml": suggest.to_yaml(m), "key": s["suggestion"]["key"], "dataset": dataset,
                      "record_type": record_type,
                      "columns": {k: {"meaning": v["meaning"], "by": v["by"]} for k, v in s["suggestion"]["columns"].items()}},
        "claude": s["claude"] or None,
    })


def add(conn, pack, connection_id: int, table: str, text: str, by: str, data_dir, values: dict | None = None,
        load: bool = True) -> dict:
    """Save a table on a connection as a dataset with this mapping (approved) and load it. The mapping's source must
    be the connection's system; its dataset names the new dataset (with `location` when it isn't the table)."""
    from . import ingest, quality

    c = _connection(conn, connection_id)
    try:
        raw = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        raise OnboardError(f"the mapping isn't YAML: {e}") from e
    if not isinstance(raw, dict) or not raw.get("dataset"):
        raise OnboardError("the mapping needs a dataset name")
    if raw.get("source") != c["system"]:
        raise OnboardError(f"the mapping's source must be the system's name, {c['system']!r} (it says {raw.get('source')!r})")
    name = raw["dataset"]
    if catalog.dataset(conn, name):
        raise OnboardError(f"there is already a dataset {name!r}: open it to change its mapping")
    catalog._parse(text)  # valid before anything is written
    extra = {k: v for k, v in (values or {}).items() if v not in (None, "")}
    if name != table:
        extra.setdefault("location", table)
    catalog.add_dataset(conn, name, c["system"], by, "added from the Sources page", connection_id=connection_id,
                        record_type=raw["record_type"], **extra)
    v = catalog.propose(conn, text, by, note="added from the Sources page", status="approved")
    conn.execute("update review_item set status = 'decided', decided_by = %s, decided_at = now() "
                 "where kind = 'mapping' and subject = %s and status = 'open'", (by, table))
    conn.commit()
    out = {"dataset": name, "mapping_version": v["id"]}
    if load:
        stats = ingest.ingest(conn, data_dir, [name], trigger="api")
        out["load"] = stats
        run = catalog.runs(conn, name, 1)
        out["run"] = run[0] if run else None
        out["health"] = (quality.health(conn, name) or [None])[0]
    return plain(out)

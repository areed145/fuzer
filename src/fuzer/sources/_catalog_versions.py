"""Mapping version validation, approval, discard, and rollback."""

import yaml

from ._catalog_common import CatalogError, _audit
from . import _catalog_entities as entities


def current_mappings(conn) -> list[dict]:
    """The approved mapping in use per source and record type (the latest)."""
    return [dict(r) for r in conn.execute(
        "select distinct on (source, record_type) * from mapping_version where status = 'approved' "
        "order by source, record_type, id desc")]


def versions(conn, source: str, record_type: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select * from mapping_version where source = %s and record_type = %s order by id desc", (source, record_type))]


def _parse(text: str):
    from .mappings import Mapping, MappingError

    try:
        raw = yaml.safe_load(text or "")
        return raw, Mapping(raw if isinstance(raw, dict) else {})
    except (yaml.YAMLError, MappingError) as e:
        raise CatalogError(f"mapping is not valid: {e}") from e


def _dataset_row(conn, raw: dict, by: str, status: str = "active") -> dict:
    """The dataset a table mapping names: created when new, its where-fields updated from the mapping."""
    from ._catalog_common import CATALOG, WHERE

    name = raw["dataset"]
    where = {k: raw.get(k) for k in WHERE if raw.get(k) is not None}
    where["record_type"] = raw["record_type"]
    for k in CATALOG:
        if k in (raw.get("catalog") or {}):
            where[k] = raw["catalog"][k]
    if (cur := conn.execute("select name from source_dataset where name = %s", (name,)).fetchone()) is None:
        return entities.add_dataset(conn, name, raw["source"], by, status=where.pop("status", status), **where)
    return entities.update_dataset(conn, cur["name"], by, **{k: v for k, v in where.items() if k != "status"})


def propose(conn, text: str, by: str, note: str | None = None, status: str = "draft", parent_id: int | None = None,
            seed_sha: str | None = None) -> dict:
    """Store a mapping version (a draft unless `status` says approved). Raises CatalogError when invalid."""
    raw, m = _parse(text)
    if status == "approved" and m.dataset:
        d = _dataset_row(conn, raw, by)
    else:
        d = conn.execute("select id from source_dataset where name = %s", (m.dataset,)).fetchone() if m.dataset else None
        d = d or _reader_dataset(conn, m.source, m.record_type)
    row = conn.execute(
        "insert into mapping_version (source, record_type, dataset, yaml, status, author, note, parent_id, seed_sha, "
        "approved_by, approved_at, dataset_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "case when %s then now() end, %s) returning *",
        (m.source, m.record_type, m.dataset, text, status, by, note, parent_id, seed_sha,
         by if status == "approved" else None, status == "approved", d["id"] if d else None)).fetchone()
    _audit(conn, by, "mapping", {"id": row["id"], "name": f"{m.source}/{m.record_type}"},
           "approve" if status == "approved" else "propose", new={"yaml": text, "note": note})
    return dict(row)


def _reader_dataset(conn, source: str, record_type: str) -> dict | None:
    from .ingest import READERS

    for name, r in READERS.items():
        if r.source == source and record_type in r.record_types:
            return conn.execute("select id from source_dataset where name = %s", (name,)).fetchone()
    return None


def approve(conn, version_id: int, by: str, reason: str | None = None) -> dict:
    """Make a draft the version in use (its table's where-fields applied to the dataset)."""
    v = conn.execute("select * from mapping_version where id = %s", (version_id,)).fetchone()
    if not v:
        raise CatalogError(f"no mapping version {version_id}")
    if v["status"] == "approved":
        return dict(v)
    raw, m = _parse(v["yaml"])
    d = _dataset_row(conn, raw, by) if m.dataset else _reader_dataset(conn, m.source, m.record_type)
    row = conn.execute("update mapping_version set status = 'approved', approved_by = %s, approved_at = now(), "
                       "dataset_id = coalesce(dataset_id, %s) where id = %s returning *",
                       (by, d["id"] if d else None, version_id)).fetchone()
    _audit(conn, by, "mapping", {"id": version_id, "name": f"{m.source}/{m.record_type}"}, "approve", reason=reason)
    return dict(row)


def discard(conn, version_id: int, by: str, reason: str | None = None) -> dict:
    """Set a draft aside (status retired): it stays in the history but isn't offered any more."""
    v = conn.execute("select * from mapping_version where id = %s", (version_id,)).fetchone()
    if not v or v["status"] != "draft":
        raise CatalogError(f"version {version_id} isn't a draft")
    conn.execute("update mapping_version set status = 'retired' where id = %s", (version_id,))
    _audit(conn, by, "mapping", {"id": version_id, "name": f"{v['source']}/{v['record_type']}"}, "discard",
           reason=reason)
    return {"discarded": version_id}


def rollback(conn, version_id: int, by: str, reason: str | None = None) -> dict:
    """Put an older version back in use, as a new approved version copying it (history stays linear)."""
    v = conn.execute("select * from mapping_version where id = %s", (version_id,)).fetchone()
    if not v:
        raise CatalogError(f"no mapping version {version_id}")
    row = propose(conn, v["yaml"], by, note=reason or f"rollback to version {version_id}", status="approved",
                  parent_id=version_id)
    _audit(conn, by, "mapping", {"id": row["id"], "name": f"{v['source']}/{v['record_type']}"}, "rollback",
           old={"version": version_id}, reason=reason)
    return row

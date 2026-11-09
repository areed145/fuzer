"""Systems, connections, datasets, and their change history."""

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain
from ._catalog_common import (CONNECTION_FIELDS, DATASET_FIELDS, SYSTEM_FIELDS, SYSTEM_KINDS, CatalogError, _audit,
                              _changes, _check_dataset)


def systems(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("select * from source_system order by name")]


def system(conn, name: str) -> dict | None:
    r = conn.execute("select * from source_system where name = %s", (name,)).fetchone()
    return dict(r) if r else None


def datasets(conn, status: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Datasets by name, each with its system's name (`source`) and its connection (type, config)."""
    return {r["name"]: dict(r) for r in conn.execute(
        """
        select d.*, s.name as source, s.decimal as system_decimal, s.date_format as system_date_format,
               s.timezone as system_timezone, s.encoding as system_encoding, s.owner as system_owner,
               c.type as connection_type, c.name as connection_name, c.config as connection_config,
               c.secrets as connection_secrets, c.enabled as connection_enabled
        from source_dataset d join source_system s on s.id = d.system_id
        left join source_connection c on c.id = d.connection_id
        where %(st)s::text[] is null or d.status = any(%(st)s)
        order by d.name
        """, {"st": list(status) if status else None})}


def dataset(conn, name: str) -> dict | None:
    return datasets(conn).get(name)


def runs(conn, name: str | None = None, limit: int = 50) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select * from source_run where %(n)s::text is null or dataset = %(n)s order by id desc limit %(l)s",
        {"n": name, "l": limit})]


def audit(conn, obj: str | None = None, object_id: int | None = None, limit: int = 100) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select * from source_audit where (%(o)s::text is null or object = %(o)s) "
        "and (%(i)s::bigint is null or object_id = %(i)s) order by id desc limit %(l)s",
        {"o": obj, "i": object_id, "l": limit})]


def add_system(conn, name: str, by: str, reason: str | None = None, files: bool = True, **values) -> dict:
    """A system of record; `files` also gives it a connection to the project's data folder (the seed's way)."""
    if system(conn, name):
        raise CatalogError(f"system {name!r} already exists")
    _changes({}, values, SYSTEM_FIELDS)
    cols = ["name", *values]
    row = conn.execute(f"insert into source_system ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                       "returning *", [name, *values.values()]).fetchone()
    _audit(conn, by, "system", row, "create", new=dict(row), reason=reason)
    if files:
        add_connection(conn, name, "files", "folder", by, reason="every system starts with the project's data folder")
    return dict(row)


def update_system(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    old = system(conn, name)
    if not old:
        raise CatalogError(f"no system {name!r}")
    before, after = _changes(old, values, SYSTEM_FIELDS)
    if not after:
        return old
    sets = ", ".join(f"{k} = %s" for k in after)
    row = conn.execute(f"update source_system set {sets}, updated_at = now() where id = %s returning *",
                       [*after.values(), old["id"]]).fetchone()
    _audit(conn, by, "system", row, "update", before, after, reason)
    return dict(row)


def connections(conn, system_name: str | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select c.*, s.name as system from source_connection c join source_system s on s.id = c.system_id "
        "where %(s)s::text is null or s.name = %(s)s order by s.name, c.name", {"s": system_name})]


def connection(conn, connection_id: int) -> dict | None:
    r = conn.execute("select c.*, s.name as system from source_connection c join source_system s on s.id = c.system_id "
                     "where c.id = %s", (connection_id,)).fetchone()
    return dict(r) if r else None


def add_connection(conn, system_name: str, name: str, type_: str, by: str, config: dict | None = None,
                   secrets: dict | None = None, reason: str | None = None) -> dict:
    s = system(conn, system_name)
    if not s:
        raise CatalogError(f"no system {system_name!r}")
    from . import connections as types

    types.check(type_, config or {}, secrets or {})
    row = conn.execute("insert into source_connection (system_id, name, type, config, secrets) values (%s, %s, %s, %s, %s) "
                       "returning *", (s["id"], name, type_, Jsonb(config or {}), Jsonb(secrets or {}))).fetchone()
    _audit(conn, by, "connection", row, "create", new=dict(row), reason=reason)
    return dict(row)


def update_connection(conn, connection_id: int, by: str, reason: str | None = None, **values) -> dict:
    old = connection(conn, connection_id)
    if not old:
        raise CatalogError(f"no connection {connection_id}")
    before, after = _changes(old, values, CONNECTION_FIELDS)
    if not after:
        return old
    from . import connections as types

    types.check(after.get("type", old["type"]), after.get("config", old["config"]), after.get("secrets", old["secrets"]))
    sets = ", ".join(f"{k} = %s" for k in after)
    row = conn.execute(f"update source_connection set {sets}, updated_at = now(), last_test = null, "
                       "last_tested_at = null where id = %s returning *",
                       [Jsonb(v) if isinstance(v, dict) else v for v in after.values()] + [connection_id]).fetchone()
    _audit(conn, by, "connection", row, "update", before, after, reason)
    return dict(row)


def _default_connection(conn, system_id: int) -> int | None:
    r = conn.execute("select id from source_connection where system_id = %s and enabled order by id limit 1",
                     (system_id,)).fetchone()
    return r["id"] if r else None


def add_dataset(conn, name: str, system_name: str, by: str, reason: str | None = None, **values) -> dict:
    if conn.execute("select 1 from source_dataset where name = %s", (name,)).fetchone():
        raise CatalogError(f"dataset {name!r} already exists")
    s = system(conn, system_name) or add_system(conn, system_name, by, kind=SYSTEM_KINDS.get(system_name),
                                                reason=f"for dataset {name}")
    if (via := values.pop("connection", None)) is not None:  # a connection of this system by name (#417)
        r = conn.execute("select id from source_connection where system_id = %s and name = %s", (s["id"], str(via))).fetchone()
        if not r:
            raise CatalogError(f"system {system_name!r} has no connection {via!r}")
        values["connection_id"] = r["id"]
    _changes({}, values, (*DATASET_FIELDS, "seeded"))
    _check_dataset(values)
    values.setdefault("connection_id", _default_connection(conn, s["id"]))
    if values.get("watermark") and "load_mode" not in values:
        values["load_mode"] = "append"
    cols = ["name", "system_id", *values]
    row = conn.execute(f"insert into source_dataset ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) returning *",
                       [name, s["id"], *(Jsonb(v) if isinstance(v, dict) else v for v in values.values())]).fetchone()
    _audit(conn, by, "dataset", row, "create", new={k: v for k, v in dict(row).items() if k in DATASET_FIELDS},
           reason=reason)
    return dict(row)


def update_dataset(conn, name: str, by: str, reason: str | None = None, action: str = "update", **values) -> dict:
    old = conn.execute("select * from source_dataset where name = %s", (name,)).fetchone()
    if not old:
        raise CatalogError(f"no dataset {name!r}")
    _check_dataset(values)
    before, after = _changes(dict(old), values, DATASET_FIELDS)
    if not after:
        return dict(old)
    sets = ", ".join(f"{k} = %s" for k in after)
    row = conn.execute(f"update source_dataset set {sets}, updated_at = now() where id = %s returning *",
                       [*after.values(), old["id"]]).fetchone()
    _audit(conn, by, "dataset", row, action, before, after, reason)
    return dict(row)


def pause(conn, name: str, by: str, reason: str | None = None) -> dict:
    """Stop loading a dataset; its records and their entities stay."""
    return update_dataset(conn, name, by, reason, action="pause", status="paused")


def resume(conn, name: str, by: str, reason: str | None = None) -> dict:
    return update_dataset(conn, name, by, reason, action="resume", status="active")


def produces(d: dict) -> list[tuple[str, str]]:
    """The (source, record type) pairs a dataset's records land as."""
    if d.get("reader"):
        from .ingest import READERS

        r = READERS.get(d["reader"])
        return [(r.source, t) for t in r.record_types] if r else []
    return [(d["source"], d["record_type"])] if d.get("record_type") else []


def retire_preview(conn, name: str) -> dict:
    """What retiring a dataset with `retract` would take away: its records, and the entities only it holds."""
    d = dataset(conn, name)
    if not d:
        raise CatalogError(f"no dataset {name!r}")
    pairs = produces(d)
    if not pairs:
        return {"dataset": name, "records": 0, "entities_only_here": 0}
    srcs, types = [p[0] for p in pairs], [p[1] for p in pairs]
    rec = conn.execute("select count(*) as n from source_record where (source, record_type) in "
                       "(select * from unnest(%s::text[], %s::text[]))", (srcs, types)).fetchone()["n"]
    only = conn.execute(
        """
        with mine as (select distinct fuzer_id from crosswalk where (source, record_type) in
                      (select * from unnest(%(s)s::text[], %(t)s::text[])))
        select count(*) as n from mine m where not exists (
            select 1 from crosswalk x where x.fuzer_id = m.fuzer_id
            and (x.source, x.record_type) not in (select * from unnest(%(s)s::text[], %(t)s::text[])))
        """, {"s": srcs, "t": types}).fetchone()["n"]
    return {"dataset": name, "records": rec, "entities_only_here": only,
            "record_types": [f"{s}/{t}" for s, t in pairs]}


def retire(conn, name: str, by: str, retract: bool, reason: str | None = None) -> dict:
    """Stop loading a dataset for good. `retract` also removes its records (kept in source_record_history) and
    retires its mappings, so the next rebuild drops what only it said; otherwise its records stay as history."""
    preview = retire_preview(conn, name)
    d = update_dataset(conn, name, by, reason, action="retire_retract" if retract else "retire_keep", status="retired")
    if retract:
        for source, rtype in produces(dataset(conn, name)):
            conn.execute("insert into source_record_history (source, record_type, source_key, payload, first_seen, change) "
                         "select source, record_type, source_key, payload, first_seen, 'removed' from source_record "
                         "where source = %s and record_type = %s", (source, rtype))
            conn.execute("delete from source_record where source = %s and record_type = %s", (source, rtype))
            conn.execute("update mapping_version set status = 'retired' where source = %s and record_type = %s "
                         "and status = 'approved'", (source, rtype))
        conn.execute("update source_dataset set fingerprint = null, schema = null, position = null, rows = null "
                     "where id = %s", (d["id"],))
    return {**preview, "status": "retired", "retracted": retract}


def delete_dataset(conn, name: str, by: str, reason: str | None = None) -> dict:
    """Undo adding a dataset: its records are removed (kept in source_record_history), the mapping versions of
    record types only it delivers are deleted, and the dataset with its runs, profiles and checks goes. For a
    source that is just finished, retire it instead (its history stays)."""
    d = dataset(conn, name)
    if not d:
        raise CatalogError(f"no dataset {name!r}")
    preview = retire_preview(conn, name)
    others = {p for n, x in datasets(conn).items() if n != name for p in produces(x)}
    for source, rtype in produces(d):
        conn.execute("insert into source_record_history (source, record_type, source_key, payload, first_seen, change) "
                     "select source, record_type, source_key, payload, first_seen, 'removed' from source_record "
                     "where source = %s and record_type = %s", (source, rtype))
        conn.execute("delete from source_record where source = %s and record_type = %s", (source, rtype))
        if (source, rtype) not in others:
            conn.execute("delete from mapping_version where source = %s and record_type = %s", (source, rtype))
    conn.execute("update review_item set status = 'stale' where kind in ('schema', 'check') and subject = %s "
                 "and status = 'open'", (name,))
    conn.execute("delete from source_dataset where id = %s", (d["id"],))
    _audit(conn, by, "dataset", d, "delete", old={k: v for k, v in d.items() if k in DATASET_FIELDS}, reason=reason)
    return {**preview, "deleted": name}


def delete_connection(conn, connection_id: int, by: str, reason: str | None = None) -> dict:
    c = connection(conn, connection_id)
    if not c:
        raise CatalogError(f"no connection {connection_id}")
    used = [r["name"] for r in conn.execute("select name from source_dataset where connection_id = %s", (connection_id,))]
    if used:
        raise CatalogError(f"datasets read through it: {', '.join(used)}; delete them or move them first")
    conn.execute("delete from source_connection where id = %s", (connection_id,))
    _audit(conn, by, "connection", c, "delete", old={k: c[k] for k in CONNECTION_FIELDS}, reason=reason)
    return {"deleted": c["name"]}


def delete_system(conn, name: str, by: str, reason: str | None = None) -> dict:
    s = system(conn, name)
    if not s:
        raise CatalogError(f"no system {name!r}")
    used = [r["name"] for r in conn.execute("select name from source_dataset where system_id = %s", (s["id"],))]
    if used:
        raise CatalogError(f"it has datasets: {', '.join(used)}; delete them first")
    conn.execute("delete from source_system where id = %s", (s["id"],))  # its connections go with it
    _audit(conn, by, "system", s, "delete", old={k: s[k] for k in SYSTEM_FIELDS}, reason=reason)
    return {"deleted": name}

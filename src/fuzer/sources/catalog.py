"""The source catalog (#225): every system of record, connection, dataset and mapping version is a row, so a
deployed Fuzer is set up and changed from the UI, API and CLI, with history, instead of in repository files.

    source_system      sap, pi, ec, ... : owner, how it writes dates and numbers, survivorship rank
    source_connection  how to reach a system (folder, PI Web API, ADX); secrets as references
    source_dataset     one table or feed: where it is, its key and watermark, how it loads, its state
    mapping_version    what the columns mean, per source and record type; the latest approved one is in use
    source_run         one row per load that changed something
    source_audit       every change above: who, when, old and new, why

The built-in mapping files (src/fuzer/mappings/) and nested-export readers are seed data. `seed` imports them
into a database, idempotently: it never overwrites what a person changed, and when a built-in file moved
ahead of a version someone edited, it leaves the new built-in as a draft and says so. A database with an
empty catalog is seeded the first time anything reads it.

    fuzer sources seed
    fuzer sources export --out review/
    fuzer sources import review/sap_equipment.yaml     # a draft, approved with `fuzer sources approve <id>`
"""

import hashlib
import os
import warnings
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain
from ..resources import MAPPINGS as DEFAULT_DIR

SEED = "seed"  # the author of versions and rows that came from the built-in files
LOAD_MODES = ("snapshot", "incremental", "append")
DRIFT_POLICIES = ("hold_any", "hold_breaking", "ignore")
STATUSES = ("draft", "active", "paused", "retired")
SYSTEM_KINDS = {"sap": "erp", "jde": "erp", "maximo": "erp", "pi": "historian", "pi_af": "historian",
                "adx": "historian", "dcs": "historian", "ec": "allocation", "edms": "documents", "pid": "documents",
                "oem": "documents", "gis": "gis", "lims": "lab", "inspection": "integrity", "iow": "integrity",
                "oplog": "logs"}
# Where a dataset is and how it loads: on source_dataset, and in an exported mapping file's top level.
WHERE = ("location", "connector", "key", "watermark")
CATALOG = ("load_mode", "drift_policy", "status", "schedule", "expected_hours", "owner", "notes")
SYSTEM_FIELDS = ("kind", "description", "owner", "steward", "timezone", "encoding", "decimal", "date_format", "rank",
                 "enabled", "notes")
CONNECTION_FIELDS = ("name", "type", "config", "secrets", "enabled")
DATASET_FIELDS = ("connection_id", "record_type", "reader", *WHERE, *CATALOG)


class CatalogError(ValueError):
    pass


# ---- reading -------------------------------------------------------------------------------------

def ensure(conn) -> None:
    """Seed an empty catalog, and give any approved mapping that names a dataset without a row (one stored
    before #225, or inserted by hand) its dataset."""
    if not conn.execute("select exists (select 1 from source_system) as x").fetchone()["x"]:
        seed(conn)
        return
    from . import connections

    connections.seed(conn)  # a PI or ADX server set in the old settings since the last look
    stray = conn.execute(
        "select distinct on (source, record_type) * from mapping_version m where status = 'approved' "
        "and dataset is not null and not exists (select 1 from source_dataset d where d.name = m.dataset) "
        "order by source, record_type, id desc").fetchall()
    for r in stray:
        raw = yaml.safe_load(r["yaml"]) or {}
        d = _dataset_row(conn, raw, by=r["approved_by"] or r["author"] or "anonymous")
        conn.execute("update mapping_version set dataset_id = %s where id = %s", (d["id"], r["id"]))


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


def current_mappings(conn) -> list[dict]:
    """The approved mapping in use per source and record type (the latest)."""
    return [dict(r) for r in conn.execute(
        "select distinct on (source, record_type) * from mapping_version where status = 'approved' "
        "order by source, record_type, id desc")]


def versions(conn, source: str, record_type: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select * from mapping_version where source = %s and record_type = %s order by id desc", (source, record_type))]


def produces(d: dict) -> list[tuple[str, str]]:
    """The (source, record type) pairs a dataset's records land as."""
    if d.get("reader"):
        from .ingest import READERS

        r = READERS.get(d["reader"])
        return [(r.source, t) for t in r.record_types] if r else []
    return [(d["source"], d["record_type"])] if d.get("record_type") else []


def runs(conn, name: str | None = None, limit: int = 50) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select * from source_run where %(n)s::text is null or dataset = %(n)s order by id desc limit %(l)s",
        {"n": name, "l": limit})]


def audit(conn, obj: str | None = None, object_id: int | None = None, limit: int = 100) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select * from source_audit where (%(o)s::text is null or object = %(o)s) "
        "and (%(i)s::bigint is null or object_id = %(i)s) order by id desc limit %(l)s",
        {"o": obj, "i": object_id, "l": limit})]


# ---- changing (every change is audited) ----------------------------------------------------------

def _audit(conn, by: str, obj: str, row: dict | None, action: str, old: dict | None = None, new: dict | None = None,
           reason: str | None = None) -> None:
    conn.execute("insert into source_audit (by, object, object_id, name, action, old, new, reason) "
                 "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (by, obj, (row or {}).get("id"), (row or {}).get("name"), action,
                  Jsonb(plain(old)) if old is not None else None, Jsonb(plain(new)) if new is not None else None, reason))


def _changes(old: dict, values: dict, fields: tuple[str, ...]) -> tuple[dict, dict]:
    unknown = set(values) - set(fields)
    if unknown:
        raise CatalogError(f"unknown field(s) {sorted(unknown)}; one of {list(fields)}")
    new = {k: v for k, v in values.items() if old.get(k) != v}
    return {k: old.get(k) for k in new}, new


def _check_dataset(values: dict) -> None:
    for field, allowed in (("load_mode", LOAD_MODES), ("drift_policy", DRIFT_POLICIES), ("status", STATUSES)):
        if field in values and values[field] not in allowed:
            raise CatalogError(f"{field} must be one of {list(allowed)}")
    if "key" in values and isinstance(values["key"], str):
        values["key"] = [values["key"]]
    if values.get("connector"):
        from .mappings import CONNECTORS

        if values["connector"] not in CONNECTORS:
            raise CatalogError(f"unknown connector {values['connector']!r}; one of {list(CONNECTORS)}")
    if "schedule" in values and values["schedule"]:
        if len(str(values["schedule"]).split()) != 5:
            raise CatalogError("schedule is a cron expression with five fields, e.g. '0 * * * *'")


def add_system(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    if system(conn, name):
        raise CatalogError(f"system {name!r} already exists")
    _changes({}, values, SYSTEM_FIELDS)
    cols = ["name", *values]
    row = conn.execute(f"insert into source_system ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                       "returning *", [name, *values.values()]).fetchone()
    _audit(conn, by, "system", row, "create", new=dict(row), reason=reason)
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
    row = conn.execute(f"update source_connection set {sets}, updated_at = now() where id = %s returning *",
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


# ---- mapping versions ----------------------------------------------------------------------------

def _parse(text: str):
    from .mappings import Mapping, MappingError

    try:
        raw = yaml.safe_load(text or "")
        return raw, Mapping(raw if isinstance(raw, dict) else {})
    except (yaml.YAMLError, MappingError) as e:
        raise CatalogError(f"mapping is not valid: {e}") from e


def _dataset_row(conn, raw: dict, by: str, status: str = "active") -> dict:
    """The dataset a table mapping names: created when new, its where-fields updated from the mapping."""
    name = raw["dataset"]
    where = {k: raw.get(k) for k in WHERE if raw.get(k) is not None}
    where["record_type"] = raw["record_type"]
    for k in CATALOG:
        if k in (raw.get("catalog") or {}):
            where[k] = raw["catalog"][k]
    if (cur := conn.execute("select name from source_dataset where name = %s", (name,)).fetchone()) is None:
        return add_dataset(conn, name, raw["source"], by, status=where.pop("status", status), **where)
    return update_dataset(conn, cur["name"], by, **{k: v for k, v in where.items() if k != "status"})


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


# ---- seed, export, import ------------------------------------------------------------------------

_WARNED: set[str] = set()


def seed_dirs() -> list[Path]:
    """The built-in mapping files, then $FUZER_MAPPINGS folders (deprecated: put mappings in the database)."""
    extra = os.environ.get("FUZER_MAPPINGS", "")
    if extra and extra not in _WARNED:
        _WARNED.add(extra)
        warnings.warn("FUZER_MAPPINGS is deprecated (#225): mappings live in the database. Import these files with "
                      "`fuzer sources import <file>` (or seed them with `fuzer sources seed`)", DeprecationWarning,
                      stacklevel=3)
    return [DEFAULT_DIR, *(Path(p) for p in extra.split(os.pathsep) if p)]


def seed_files() -> list[tuple[dict, str]]:
    out = {}
    for d in seed_dirs():
        for f in sorted(d.glob("*.yaml")):
            text = f.read_text()
            raw = yaml.safe_load(text)
            out[(raw["source"], raw["record_type"])] = (raw, text)  # later folders win, as before
    return list(out.values())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def seed(conn, by: str = SEED) -> dict:
    """Import the built-in mapping files and readers. Rows and versions a person changed are left alone; a
    built-in file that changed after someone edited its mapping becomes a draft (reported as `ahead`)."""
    from .ingest import READERS

    report = {"systems": [], "datasets": [], "mappings": [], "drafts": [], "ahead": [], "updated": []}
    files = seed_files()
    names = {raw["source"] for raw, _ in files} | {r.source for r in READERS.values()}
    for name in sorted(names):
        if not system(conn, name):
            add_system(conn, name, by, kind=SYSTEM_KINDS.get(name), reason="seed")
            report["systems"].append(name)
    for name, r in READERS.items():
        if not conn.execute("select 1 from source_dataset where name = %s", (name,)).fetchone():
            add_dataset(conn, name, r.source, by, reader=name, seeded={"reader": name}, reason="seed")
            report["datasets"].append(name)
    for raw, text in files:
        if raw.get("dataset"):
            _seed_dataset(conn, raw, by, report)
        sha, label = _sha(text), f"{raw['source']}/{raw['record_type']}"
        key = (raw["source"], raw["record_type"])
        cur = conn.execute("select * from mapping_version where source = %s and record_type = %s and status = 'approved' "
                           "order by id desc limit 1", key).fetchone()
        last_seed = conn.execute("select * from mapping_version where source = %s and record_type = %s "
                                 "and seed_sha is not null order by id desc limit 1", key).fetchone()
        if cur is None:
            propose(conn, text, by, note="built-in", status="approved", seed_sha=sha)
            report["mappings"].append(label)
        elif last_seed and last_seed["seed_sha"] == sha:  # the built-in hasn't moved since the last seed
            if last_seed["status"] == "draft":
                report["ahead"].append(label)  # still waiting for someone to look at it
        elif cur["author"] == SEED:  # nobody edited it: follow the built-in
            propose(conn, text, by, note="built-in changed", status="approved", parent_id=cur["id"], seed_sha=sha)
            report["updated"].append(label)
        else:
            propose(conn, text, by, note="built-in changed after this mapping was edited", status="draft",
                    parent_id=cur["id"], seed_sha=sha)
            report["drafts"].append(label)
            report["ahead"].append(label)
    _adopt_old_state(conn)
    from . import connections

    report["connections"] = connections.seed(conn, by)
    return {k: v for k, v in report.items() if v}


def _seed_dataset(conn, raw: dict, by: str, report: dict) -> None:
    want = {k: raw.get(k) for k in WHERE if raw.get(k) is not None}
    if isinstance(want.get("key"), str):
        want["key"] = [want["key"]]
    want["record_type"] = raw["record_type"]
    want["load_mode"] = "append" if raw.get("watermark") else "snapshot"
    want |= {k: v for k, v in (raw.get("catalog") or {}).items() if k in CATALOG}
    cur = conn.execute("select * from source_dataset where name = %s", (raw["dataset"],)).fetchone()
    if cur is None:
        add_dataset(conn, raw["dataset"], raw["source"], by, seeded=want, reason="seed", **want)
        report["datasets"].append(raw["dataset"])
        return
    seeded = cur["seeded"] or {}
    # only what still holds the seed's value follows the seed; a person's change stays
    follow = {k: v for k, v in want.items() if cur[k] != v and (cur[k] == seeded.get(k) or (k not in seeded and cur[k] is None))}
    if follow:
        update_dataset(conn, raw["dataset"], by, reason="built-in changed", **follow)
        report["updated"].append(raw["dataset"])
    if seeded != want:
        conn.execute("update source_dataset set seeded = %s where id = %s", (Jsonb(want), cur["id"]))


def _adopt_old_state(conn) -> None:
    """Load state from before #225 (the dataset_state table) onto the datasets, once."""
    if not conn.execute("select to_regclass('dataset_state_old') as t").fetchone()["t"]:
        return
    conn.execute("""
        update source_dataset d set loaded_with = o.connector, fingerprint = o.fingerprint, schema = o.schema,
               position = o.position, rows = o.rows, loaded_at = o.loaded_at
        from dataset_state_old o where o.dataset = d.name
    """)
    conn.execute("drop table dataset_state_old")


def export(conn, names: list[str] | None = None) -> dict[str, str]:
    """The catalog as YAML files by name: one mapping file per mapping in use (its dataset's where-fields and a
    `catalog:` block with how it loads), and systems.yaml with the systems and connections."""
    ds = datasets(conn)
    by_type = {(d["source"], d["record_type"]): d for d in ds.values() if d["record_type"]}
    out = {}
    for v in current_mappings(conn):
        raw = yaml.safe_load(v["yaml"]) or {}
        d = by_type.get((v["source"], v["record_type"]))
        name = d["name"] if d else f"{v['source']}_{v['record_type']}"
        if names and name not in names and v["source"] not in names:
            continue
        text = v["yaml"]
        if d:
            where = {k: d[k] for k in WHERE if d[k] is not None}
            if where.get("key"):
                where["key"] = where["key"][0] if len(where["key"]) == 1 else list(where["key"])
            block = {k: d[k] for k in CATALOG if d[k] not in (None, "") and k != "status"} | {"status": d["status"]}
            if {k: raw.get(k) for k in WHERE if raw.get(k) is not None} == where and raw.get("dataset") == d["name"] \
                    and "catalog" not in raw:  # unchanged where-fields: keep the file as written (comments too)
                text = text.rstrip("\n") + "\n" + yaml.safe_dump({"catalog": plain(block)}, sort_keys=False)
            else:
                raw = {**{k: x for k, x in raw.items() if k not in (*WHERE, "dataset", "catalog")}}
                head = {"source": raw.pop("source"), "record_type": raw.pop("record_type"), "shape": raw.pop("shape"),
                        "dataset": d["name"], **where}
                text = yaml.safe_dump(plain({**head, **raw, "catalog": block}), sort_keys=False, allow_unicode=True)
        out[f"{name}.yaml"] = text
    sys_rows = [s for s in systems(conn) if not names or s["name"] in names or any(
        d["source"] == s["name"] and d["name"] in names for d in ds.values())]
    conns = connections(conn)
    out["systems.yaml"] = yaml.safe_dump(plain({"systems": [
        {"name": s["name"], **{k: s[k] for k in SYSTEM_FIELDS if s[k] is not None},
         "connections": [{k: c[k] for k in CONNECTION_FIELDS} for c in conns if c["system_id"] == s["id"]]}
        for s in sys_rows]}), sort_keys=False, allow_unicode=True)
    return out


def import_(conn, text: str, by: str, note: str | None = None) -> dict:
    """A mapping file becomes a draft version (approve it to use it); a systems.yaml updates systems and
    connections (audited)."""
    raw = yaml.safe_load(text or "") or {}
    if "systems" in raw:
        done = []
        for s in raw["systems"]:
            vals = {k: s[k] for k in SYSTEM_FIELDS if k in s}
            (update_system if system(conn, s["name"]) else add_system)(conn, s["name"], by, reason=note, **vals)
            have = {c["name"]: c for c in connections(conn, s["name"])}
            for c in s.get("connections") or []:
                if c["name"] in have:
                    update_connection(conn, have[c["name"]]["id"], by, reason=note,
                                      **{k: c[k] for k in CONNECTION_FIELDS if k in c and k != "name"})
                else:
                    add_connection(conn, s["name"], c["name"], c["type"], by, c.get("config"), c.get("secrets"), note)
            done.append(s["name"])
        return {"systems": done}
    v = propose(conn, text, by, note=note or "imported")
    return {"draft": v["id"], "mapping": f"{v['source']}/{v['record_type']}"}

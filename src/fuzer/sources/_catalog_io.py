"""Seed data and import/export operations for the source catalog."""

import hashlib
import os
import warnings
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain
from ..resources import MAPPINGS as DEFAULT_DIR
from . import _catalog_entities as entities
from . import _catalog_versions as versions
from ._catalog_common import (
    CATALOG,
    CONNECTION_FIELDS,
    SEED,
    SYSTEM_FIELDS,
    SYSTEM_KINDS,
    WHERE,
)

_WARNED: set[str] = set()


def ensure(conn, seed_fn) -> None:
    """Seed an empty catalog and reconcile mappings that predate their dataset rows."""
    if not conn.execute("select exists (select 1 from source_system) as x").fetchone()["x"]:
        seed_fn(conn)
        return
    from . import connections as connection_types

    connection_types.seed(conn)
    stray = conn.execute(
        "select distinct on (source, record_type) * from mapping_version m where status = 'approved' "
        "and dataset is not null and not exists (select 1 from source_dataset d where d.name = m.dataset) "
        "order by source, record_type, id desc").fetchall()
    for r in stray:
        raw = yaml.safe_load(r["yaml"]) or {}
        d = versions._dataset_row(conn, raw, by=r["approved_by"] or r["author"] or "anonymous")
        conn.execute("update mapping_version set dataset_id = %s where id = %s", (d["id"], r["id"]))


def seed_dirs(warned: set[str] = _WARNED) -> list[Path]:
    """The built-in mapping files, then $FUZER_MAPPINGS folders (deprecated: put mappings in the database)."""
    extra = os.environ.get("FUZER_MAPPINGS", "")
    if extra and extra not in warned:
        warned.add(extra)
        warnings.warn("FUZER_MAPPINGS is deprecated (#225): mappings live in the database. Import these files with "
                      "`fuzer sources import <file>` (or seed them with `fuzer sources seed`)", DeprecationWarning,
                      stacklevel=3)
    return [DEFAULT_DIR, *(Path(p) for p in extra.split(os.pathsep) if p)]


def seed_files(seed_dirs_fn=seed_dirs) -> list[tuple[dict, str]]:
    out = {}
    for d in seed_dirs_fn():
        for f in sorted(d.glob("*.yaml")):
            text = f.read_text()
            raw = yaml.safe_load(text)
            out[(raw["source"], raw["record_type"])] = (raw, text)  # later folders win, as before
    return list(out.values())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def seed(conn, by: str = SEED, seed_files_fn=seed_files) -> dict:
    """Import built-in mappings and readers without overwriting rows or versions changed by a person."""
    from .ingest import READERS

    report = {"systems": [], "datasets": [], "mappings": [], "drafts": [], "ahead": [], "updated": []}
    files = seed_files_fn()
    names = {raw["source"] for raw, _ in files} | {r.source for r in READERS.values()}
    for name in sorted(names):
        if not entities.system(conn, name):
            entities.add_system(conn, name, by, kind=SYSTEM_KINDS.get(name), reason="seed")
            report["systems"].append(name)
    for name, r in READERS.items():
        if not conn.execute("select 1 from source_dataset where name = %s", (name,)).fetchone():
            entities.add_dataset(conn, name, r.source, by, reader=name, seeded={"reader": name}, reason="seed")
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
            versions.propose(conn, text, by, note="built-in", status="approved", seed_sha=sha)
            report["mappings"].append(label)
        elif last_seed and last_seed["seed_sha"] == sha:
            if last_seed["status"] == "draft":
                report["ahead"].append(label)
        elif cur["author"] == SEED:
            versions.propose(conn, text, by, note="built-in changed", status="approved", parent_id=cur["id"],
                             seed_sha=sha)
            report["updated"].append(label)
        else:
            versions.propose(conn, text, by, note="built-in changed after this mapping was edited", status="draft",
                             parent_id=cur["id"], seed_sha=sha)
            report["drafts"].append(label)
            report["ahead"].append(label)
    _adopt_old_state(conn)
    from . import connections as connection_types

    report["connections"] = connection_types.seed(conn, by)
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
        entities.add_dataset(conn, raw["dataset"], raw["source"], by, seeded=want, reason="seed", **want)
        report["datasets"].append(raw["dataset"])
        return
    seeded = cur["seeded"] or {}
    follow = {k: v for k, v in want.items()
              if cur[k] != v and (cur[k] == seeded.get(k) or (k not in seeded and cur[k] is None))}
    if follow:
        entities.update_dataset(conn, raw["dataset"], by, reason="built-in changed", **follow)
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
    """The catalog as YAML files by name, and systems.yaml with the systems and connections."""
    ds = entities.datasets(conn)
    by_type = {(d["source"], d["record_type"]): d for d in ds.values() if d["record_type"]}
    out = {}
    for v in versions.current_mappings(conn):
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
                    and "catalog" not in raw:
                text = text.rstrip("\n") + "\n" + yaml.safe_dump({"catalog": plain(block)}, sort_keys=False)
            else:
                raw = {**{k: x for k, x in raw.items() if k not in (*WHERE, "dataset", "catalog")}}
                head = {"source": raw.pop("source"), "record_type": raw.pop("record_type"), "shape": raw.pop("shape"),
                        "dataset": d["name"], **where}
                text = yaml.safe_dump(plain({**head, **raw, "catalog": block}), sort_keys=False, allow_unicode=True)
        out[f"{name}.yaml"] = text
    sys_rows = [s for s in entities.systems(conn) if not names or s["name"] in names or any(
        d["source"] == s["name"] and d["name"] in names for d in ds.values())]
    conns = entities.connections(conn)
    out["systems.yaml"] = yaml.safe_dump(plain({"systems": [
        {"name": s["name"], **{k: s[k] for k in SYSTEM_FIELDS if s[k] is not None},
         "connections": [{k: c[k] for k in CONNECTION_FIELDS} for c in conns if c["system_id"] == s["id"]]}
        for s in sys_rows]}), sort_keys=False, allow_unicode=True)
    return out


def import_(conn, text: str, by: str, note: str | None = None) -> dict:
    """A mapping file becomes a draft; systems.yaml updates systems and connections (audited)."""
    raw = yaml.safe_load(text or "") or {}
    if "systems" in raw:
        done = []
        for s in raw["systems"]:
            vals = {k: s[k] for k in SYSTEM_FIELDS if k in s}
            (entities.update_system if entities.system(conn, s["name"]) else entities.add_system)(
                conn, s["name"], by, reason=note, **vals)
            have = {c["name"]: c for c in entities.connections(conn, s["name"])}
            for c in s.get("connections") or []:
                if c["name"] in have:
                    entities.update_connection(conn, have[c["name"]]["id"], by, reason=note,
                                               **{k: c[k] for k in CONNECTION_FIELDS if k in c and k != "name"})
                else:
                    entities.add_connection(conn, s["name"], c["name"], c["type"], by, c.get("config"),
                                            c.get("secrets"), note)
            done.append(s["name"])
        return {"systems": done}
    v = versions.propose(conn, text, by, note=note or "imported")
    return {"draft": v["id"], "mapping": f"{v['source']}/{v['record_type']}"}

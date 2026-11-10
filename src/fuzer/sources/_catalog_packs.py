"""Source packs (#227): one system of record as a single YAML file, to carry it to another project or database.

A pack holds the system (owner, timezone, date format, ...), its connections (secrets only as references, never
values), and each of its datasets with how it loads, the mapping in use and its checks:

    fuzer_source_pack: 1
    system: {name: planning, kind: planning, date_format: "%d/%m/%Y", ...}
    connections:
      - {name: export, type: folder, config: {path: /lake}, secrets: {}, enabled: true}
    datasets:
      - name: project_schedules
        connection: export
        record_type: project_schedule
        load_mode: snapshot
        ...
        mappings: [{source: planning, record_type: project_schedule, yaml: "..."}]
        checks: [{kind: range, column: percent_complete, params: {min: 0, max: 100}, blocking: false}]
    exception_types:                  the work the system brings (#465, workflows.py)
      - {name: schedule_slip, label: Schedule slip, role: role:planner}
    exception_rules:
      - {name: late, dataset: project_schedules, type: schedule_slip, when: {percent_complete: {lt: 50}}, ...}

Importing is safe to repeat: what is already there and the same is left alone. A new dataset's mapping is approved
(the pack is the reviewed setup); a mapping that differs from one a person approved here becomes a draft for review,
as seeding does. A connection's config can be changed on the way in (its folder is elsewhere in this environment).
"""

import json

import yaml

from ..core.jsonsafe import plain
from . import _catalog_entities as entities
from . import _catalog_versions as versions
from ._catalog_common import (
    CATALOG,
    CONNECTION_FIELDS,
    SYSTEM_FIELDS,
    WHERE,
    CatalogError,
)

VERSION = 1


def export_pack(conn, system: str) -> str:
    """One system, its connections and datasets (with mappings in use and checks), as a pack."""
    from . import quality

    s = entities.system(conn, system)
    if not s:
        raise CatalogError(f"no system {system!r}")
    conns = {c["id"]: c for c in entities.connections(conn, system)}
    current = {(v["source"], v["record_type"]): v for v in versions.current_mappings(conn)}
    datasets = []
    for d in sorted(entities.datasets(conn).values(), key=lambda d: d["name"]):
        if d["source"] != system:
            continue
        item = {"name": d["name"]}
        if d["connection_id"] in conns:
            item["connection"] = conns[d["connection_id"]]["name"]
        for k in ("record_type", "reader", *WHERE, *CATALOG):
            if d.get(k) not in (None, "", []):
                item[k] = d[k]
        item["mappings"] = [{"source": src, "record_type": rt, "yaml": current[(src, rt)]["yaml"]}
                            for src, rt in entities.produces(d) if (src, rt) in current]
        checks = [{"kind": c["kind"], **({"column": c["column_name"]} if c["column_name"] else {}),
                   **({"params": c["params"]} if c["params"] else {}), "blocking": c["blocking"],
                   **({"note": c["note"]} if c.get("note") else {})}
                  for c in quality.checks(conn, d["name"])]
        if checks:
            item["checks"] = checks
        datasets.append(item)
    from . import workflows

    pack = {"fuzer_source_pack": VERSION,
            "system": {"name": s["name"], **{k: s[k] for k in SYSTEM_FIELDS if s.get(k) is not None}},
            "connections": [{k: c[k] for k in CONNECTION_FIELDS} for c in conns.values()],
            "datasets": datasets}
    if types := workflows.types_of(conn, system):
        pack["exception_types"] = [{"name": t["name"], "label": t["label"],
                                    **{k: t[k] for k in workflows.TYPE_FIELDS[1:]
                                       if t.get(k) is not None and t[k] != workflows._DEFAULTS.get(k)
                                       and not (k == "audience" and tuple(t[k]) == ("ioc",))}}
                                   for t in types]
    if rules := workflows.rules(conn, system):
        pack["exception_rules"] = [workflows.as_pack(r) for r in rules]
    return _dump(plain(pack))


class _Literal(str):
    pass


def _dump(pack: dict) -> str:
    """Mapping YAML as a block (|) so the pack reads like the mapping files."""
    for d in pack["datasets"]:
        for m in d.get("mappings", []):
            m["yaml"] = _Literal(m["yaml"].rstrip("\n") + "\n")

    class Dumper(yaml.SafeDumper):
        pass

    Dumper.add_representer(_Literal, lambda dumper, v: dumper.represent_scalar("tag:yaml.org,2002:str", v, style="|"))
    return yaml.dump(pack, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=110)


def import_pack(conn, text: str, by: str, reason: str | None = None,
                config: dict[str, dict] | None = None, data_dir=None) -> dict:
    """Add or bring up to date the pack's system, connections, datasets, mappings, checks, exception types and
    rules. `config` changes a connection's config on the way in ({connection name: {key: value}}), e.g. where its
    folder is here. With `data_dir`, a folder connection's folder must be there before anything is written."""
    from . import quality, workflows

    try:
        pack = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        raise CatalogError(f"the pack isn't YAML: {e}") from e
    if not isinstance(pack, dict) or "fuzer_source_pack" not in pack:
        raise CatalogError("not a source pack (no fuzer_source_pack: line); a mapping file or systems.yaml is "
                           "imported with `fuzer sources import`")
    if pack["fuzer_source_pack"] != VERSION:
        raise CatalogError(f"source pack version {pack['fuzer_source_pack']} isn't supported (this Fuzer reads {VERSION})")
    reason = reason or "imported from a source pack"
    sys_ = pack.get("system") or {}
    name = sys_.get("name")
    if not name:
        raise CatalogError("the pack names no system")
    for d in pack.get("datasets") or []:
        for m in d.get("mappings") or []:
            versions._parse(m["yaml"])  # every mapping valid before anything is written
            if m["source"] != name and not d.get("reader"):
                raise CatalogError(f"dataset {d['name']!r}: its mapping's source is {m['source']!r}, not {name!r}")
    if data_dir is not None:
        _check_folders(pack, config or {}, data_dir)
    report: dict[str, list] = {"system": [], "connections": [], "datasets": [], "mappings": [], "drafts": [],
                               "checks": [], "exception_types": [], "exception_rules": [], "unchanged": []}

    vals = {k: sys_[k] for k in SYSTEM_FIELDS if k in sys_}
    if entities.system(conn, name):
        before = entities.system(conn, name)
        entities.update_system(conn, name, by, reason, **vals)
        changed = any(before.get(k) != v for k, v in vals.items())
        report["system" if changed else "unchanged"].append(f"system {name}")
    else:
        entities.add_system(conn, name, by, reason, files=False, **vals)
        report["system"].append(f"system {name}")

    have = {c["name"]: c for c in entities.connections(conn, name)}
    for c in pack.get("connections") or []:
        cfg = {**(c.get("config") or {}), **((config or {}).get(c["name"]) or {})}
        if c["name"] in have:
            old = have[c["name"]]
            want = {"type": c["type"], "config": cfg, "secrets": c.get("secrets") or {},
                    "enabled": c.get("enabled", True)}
            if any(old.get(k) != v for k, v in want.items()):
                entities.update_connection(conn, old["id"], by, reason, **want)
                report["connections"].append(c["name"])
            else:
                report["unchanged"].append(f"connection {c['name']}")
        else:
            row = entities.add_connection(conn, name, c["name"], c["type"], by, cfg, c.get("secrets"), reason)
            if c.get("enabled") is False:
                entities.update_connection(conn, row["id"], by, reason, enabled=False)
            report["connections"].append(c["name"])

    current = {(v["source"], v["record_type"]): v for v in versions.current_mappings(conn)}
    for d in pack.get("datasets") or []:
        values = {k: d[k] for k in ("record_type", "reader", *WHERE, *CATALOG) if k in d}
        if d.get("connection"):
            values["connection"] = d["connection"]
        existing = entities.dataset(conn, d["name"])
        if existing is None:
            entities.add_dataset(conn, d["name"], name, by, reason, **values)
            report["datasets"].append(d["name"])
        else:
            if existing["source"] != name:
                raise CatalogError(f"dataset {d['name']!r} already exists here for system {existing['source']!r}")
            values.pop("connection", None)
            if d.get("connection"):
                r = conn.execute("select c.id from source_connection c join source_system s on s.id = c.system_id "
                                 "where s.name = %s and c.name = %s", (name, d["connection"])).fetchone()
                values["connection_id"] = r["id"] if r else None
            changed = {k: v for k, v in values.items() if k != "status" and existing.get(k) != v}
            if changed:
                entities.update_dataset(conn, d["name"], by, reason, **changed)
                report["datasets"].append(d["name"])
        for m in d.get("mappings") or []:
            label, cur = f"{m['source']}/{m['record_type']}", current.get((m["source"], m["record_type"]))
            if cur and cur["yaml"].strip() == m["yaml"].strip():
                report["unchanged"].append(f"mapping {label}")
            elif cur and cur["author"] != by and existing is not None:
                versions.propose(conn, m["yaml"], by, note=reason, parent_id=cur["id"])  # a person's version stays
                report["drafts"].append(label)
            else:
                versions.propose(conn, m["yaml"], by, note=reason, status="approved",
                                 parent_id=cur["id"] if cur else None)
                report["mappings"].append(label)
        same = lambda kind, col, params: (kind, col, json.dumps(plain(params or {}), sort_keys=True))
        held = {same(c["kind"], c["column_name"], c["params"]) for c in quality.checks(conn, d["name"])}
        for c in d.get("checks") or []:
            if same(c["kind"], c.get("column"), c.get("params")) in held:
                continue
            try:
                quality.add_check(conn, d["name"], c["kind"], by, column=c.get("column"), params=c.get("params"),
                                  blocking=bool(c.get("blocking")), note=c.get("note"))
            except quality.CheckError as e:
                raise CatalogError(f"dataset {d['name']!r}: {e}") from e
            report["checks"].append(f"{d['name']}: {c['kind']}" + (f" {c['column']}" if c.get("column") else ""))
    report["exception_types"] = workflows.define_types(conn, name, pack.get("exception_types") or [], by)
    held = {r["name"]: workflows.as_pack(r) for r in workflows.rules(conn, name)}
    for r in pack.get("exception_rules") or []:
        if not isinstance(r, dict) or not r.get("name"):
            raise CatalogError("each exception rule needs a name")
        values = {k: v for k, v in r.items() if k != "name"}
        new = workflows.set_rule(conn, name, r["name"], by, **values)
        if held.get(r["name"]) == workflows.as_pack(new):
            report["unchanged"].append(f"rule {r['name']}")
        else:
            report["exception_rules"].append(r["name"])
    return {k: v for k, v in report.items() if v}


def _check_folders(pack: dict, config: dict, data_dir) -> None:
    """Every folder connection's folder is there (a wrong path otherwise only shows up when it is tested)."""
    from pathlib import Path

    from .connections import ConnectionError_, Folder

    for c in pack.get("connections") or []:
        if c.get("type") != "folder" or c.get("enabled") is False:
            continue
        cfg = {**(c.get("config") or {}), **(config.get(c.get("name")) or {})}
        try:
            Folder(cfg, {}).check_path(Path(data_dir))
        except ConnectionError_ as e:
            raise CatalogError(f"connection {c.get('name')!r}: {e}; give its folder here in config") from e

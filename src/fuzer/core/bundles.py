"""Seed bundles (#376): what a fresh database needs to serve one facility, in one folder.

    <bundle>/bundle.yaml      the manifest (below)
    <bundle>/pack.yaml        the facility's asset pack: tag and well grammar, aliases, matching, policy
    <bundle>/exports/         its source exports (optional: a deployment can point the project elsewhere)

    bundle: 1                 # manifest version
    name: plant               # the project this database will hold
    description: ...
    pack: pack.yaml           # relative to the bundle
    project:                  # project config (see core.projects): data, timeseries, timezone, ...
      data: exports           # paths are relative to the bundle
      timeseries: none
    source_packs: [packs/measurement.yaml]   # optional: the source packs (sources/_catalog_packs) it brings
    roster: roster.yaml       # optional: the roster template, the human workflow roles it staffs and their seats
    operating_model:          # optional: the operating model it follows (P8.1), and its named deviations from it
      name: standard
      version: 1
      deviations: [{part: settings, key: handover.shift_times, value: "07:00,19:00", reason: ..., owner: ops_lead}]

A bundle carries its source packs, roster template and operating model reference (P1.6); `init` validates and
stores them with the project. It installs nothing and assigns nobody: people are assigned to roles by their
accounts, and a source pack is installed from Sources.

`fuzer init --bundle <folder>` validates the bundle and stores the project with its pack in the `project` table.
From then on the API, MCP tools, jobs and CLI resolve the pack from the database (`pack.current_pack`), not from
repository files. Initialising is
configuration only: it doesn't ingest, rebuild, call a model or touch workflow assignments.

The first project initialised in a database holds its register in `public`. Another project's bundle adds that
project beside it, in its own schema (`proj_<name>`), sharing accounts, access and settings; code reaches it
inside `projects.use(conn, name)`. Initialising again with the same bundle changes nothing; a different bundle
for the same project is kept out unless `upgrade` is passed.
"""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import yaml

from . import projects, settings
from .pack import clear_cache, pack_from_config

VERSIONS = {1}
NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")
PATH_FIELDS = ("data", "mappings", "cache")


class BundleError(ValueError):
    pass


def find(name: str | Path) -> Path | None:
    """A bundle's folder: `name` itself when it is a bundle folder or its bundle.yaml, else the shipped bundle of
    that name (resources.BUNDLES/<name>), else None."""
    from ..resources import BUNDLES

    p = Path(name)
    if (p / "bundle.yaml").is_file() or (p.name == "bundle.yaml" and p.is_file()):
        return p
    if isinstance(name, str) and NAME.fullmatch(name) and (BUNDLES / name / "bundle.yaml").is_file():
        return BUNDLES / name
    return None


def shipped() -> list[str]:
    """The names of the bundles shipped with this checkout."""
    from ..resources import BUNDLES

    return sorted(p.name for p in BUNDLES.iterdir() if (p / "bundle.yaml").is_file()) if BUNDLES.is_dir() else []


def read(path: Path) -> dict:
    """The bundle at `path` (its folder or its bundle.yaml), validated: {name, project config, pack, digest}."""
    path = Path(path)
    manifest = path / "bundle.yaml" if path.is_dir() else path
    if not manifest.is_file():
        raise BundleError(f"no bundle.yaml at {path}")
    root = manifest.parent.resolve()
    try:
        m = yaml.safe_load(manifest.read_text()) or {}
    except yaml.YAMLError as e:
        raise BundleError(f"{manifest} isn't valid YAML: {e}") from None
    if not isinstance(m, dict):
        raise BundleError(f"{manifest} must be a mapping")
    if m.get("bundle") not in VERSIONS:
        raise BundleError(f"{manifest}: unsupported bundle version {m.get('bundle')!r} (supported: {sorted(VERSIONS)})")
    name = m.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise BundleError(f"{manifest}: name must be lower-case letters, digits, - or _ (got {name!r})")
    pack_file = root / str(m.get("pack") or "pack.yaml")
    if not pack_file.is_file():
        raise BundleError(f"{manifest}: pack file {pack_file.name} is missing")
    try:
        raw_pack = yaml.safe_load(pack_file.read_text())
        pack = pack_from_config(raw_pack)
    except Exception as e:
        raise BundleError(f"{pack_file}: not a valid asset pack ({type(e).__name__}: {e})") from None
    project = dict(m.get("project") or {})
    if not isinstance(m.get("project", {}), dict):
        raise BundleError(f"{manifest}: project must be a mapping")
    for k in ("name", "source", "pack", "pack_config", "bundle"):
        if k in project:
            raise BundleError(f"{manifest}: project.{k} is set by the bundle itself")
    for k in PATH_FIELDS:
        if project.get(k) is not None and not Path(project[k]).is_absolute():
            project[k] = str((root / project[k]).resolve())
            if k == "data" and not Path(project[k]).is_dir() and str(project[k]).startswith(str(root) + "/"):
                raise BundleError(f"{manifest}: its exports folder {m['project'][k]} is missing")
    source_packs, raw_packs = _source_packs(manifest, root, m.get("source_packs"))
    roster = _roster(manifest, root, m.get("roster"))
    model = _operating_model(manifest, m.get("operating_model"))
    digest = hashlib.sha256(json.dumps({"manifest": m, "pack": raw_pack, "source_packs": raw_packs, "roster": roster},
                                       sort_keys=True, default=str).encode())
    cfg = {**project, "pack": pack.name, "pack_config": raw_pack,
           "bundle": {"version": m["bundle"], "name": name, "digest": digest.hexdigest()[:16],
                      "description": m.get("description") or "", "source_packs": source_packs,
                      "roster": roster, "operating_model": model}}
    return {"name": name, "config": cfg, "pack": pack, "root": str(root)}


def _yaml(manifest: Path, path: Path, what: str):
    if not path.is_file():
        raise BundleError(f"{manifest}: {what} {path.name} is missing")
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise BundleError(f"{path} isn't valid YAML: {e}") from None


def _source_packs(manifest: Path, root: Path, listed) -> tuple[list[dict], list]:
    """The source packs a bundle brings: [{system, path}] and their contents (for the digest)."""
    if listed is None:
        return [], []
    if not isinstance(listed, list) or not all(isinstance(p, str) for p in listed):
        raise BundleError(f"{manifest}: source_packs must be a list of pack files")
    out, raw = [], []
    for rel in listed:
        doc = _yaml(manifest, root / rel, "source pack")
        if not isinstance(doc, dict) or doc.get("fuzer_source_pack") != 1 or not (doc.get("system") or {}).get("name"):
            raise BundleError(f"{manifest}: {rel} is not a source pack (fuzer_source_pack: 1 with a system name)")
        out.append({"system": doc["system"]["name"], "path": str((root / rel).resolve())})
        raw.append(doc)
    return out, raw


def _roster(manifest: Path, root: Path, rel) -> list[dict]:
    """The roster template: [{role, seats}], the human workflow roles the facility staffs. Role names are
    checked against the workflow's roles where the roster is used, not here (core sits below workflow)."""
    if rel is None:
        return []
    doc = _yaml(manifest, root / str(rel), "roster template")
    roles = (doc or {}).get("roles") if isinstance(doc, dict) else None
    if not isinstance(roles, list) or not roles:
        raise BundleError(f"{manifest}: roster template {rel} must list roles: [{{role, seats}}]")
    out = []
    for r in roles:
        if not isinstance(r, dict) or not isinstance(r.get("role"), str) or not isinstance(r.get("seats", 1), int) \
                or r.get("seats", 1) < 1:
            raise BundleError(f"{manifest}: roster template {rel}: each role needs a name and a whole number of seats")
        out.append({"role": r["role"], "seats": r.get("seats", 1)})
    return out


MODEL_PARTS = ("roles", "work_types", "settings", "processes", "forms")  # workflow.operating_model.PARTS


def _operating_model(manifest: Path, ref) -> dict | None:
    """The operating model the bundle follows and the deviations from it the facility owns: {name, version,
    deviations: [{part, key, reason, owner, value?}]}. Only a setting's deviation carries a value (the bundle sets it
    when the model is adopted); a deviation in another part is named here and made through the workflow screens."""
    if ref is None:
        return None
    if not isinstance(ref, dict) or not isinstance(ref.get("name"), str) or not isinstance(ref.get("version"), int):
        raise BundleError(f"{manifest}: operating_model must name a model and its version ({{name, version}})")
    out = []
    for d in ref.get("deviations") or []:
        if not isinstance(d, dict) or d.get("part") not in MODEL_PARTS or not all(
                isinstance(d.get(k), str) and d[k].strip() for k in ("key", "reason", "owner")):
            raise BundleError(f"{manifest}: each operating_model deviation needs a part ({', '.join(MODEL_PARTS)}), "
                              "a key, a reason and an owner")
        if "value" in d and d["part"] != "settings":
            raise BundleError(f"{manifest}: only a settings deviation carries a value ({d['part']} {d['key']})")
        if "review_by" in d:  # when the facility reviews it next (YAML reads a bare date as a date)
            try:
                d = {**d, "review_by": date.fromisoformat(str(d["review_by"])).isoformat()}
            except ValueError:
                raise BundleError(f"{manifest}: {d['part']} {d['key']} review_by isn't a date (YYYY-MM-DD)") from None
        out.append({k: d[k] for k in ("part", "key", "reason", "owner", "value", "review_by") if k in d})
    return {"name": ref["name"], "version": ref["version"], "deviations": out}


def _holds_data(conn) -> bool:
    return conn.execute("select exists (select 1 from source_record) as x").fetchone()["x"]


def init(conn, path: Path, *, by: str, upgrade: bool = False) -> dict:
    """Initialise this database for the bundle's facility. Returns what happened: initialised, unchanged,
    kept (a different bundle for the same project, without `upgrade`) or upgraded."""
    b = read(path)
    name, cfg = b["name"], b["config"]
    active = projects.active(conn)
    stored = projects._db_rows(conn).get(name)
    out = {"project": name, "facility": b["pack"].facility, "pack": b["pack"].name,
           "bundle": cfg["bundle"]["digest"], "data": cfg.get("data")}
    if active and active != name:
        return add(conn, b, by=by, upgrade=upgrade, out=out, stored=stored)
    if stored is not None and active == name:
        if stored.get("bundle", {}).get("digest") == cfg["bundle"]["digest"]:
            return {**out, "status": "unchanged"}
        if not upgrade:
            return {**out, "status": "kept", "stored_bundle": stored.get("bundle", {}).get("digest"),
                    "note": "the stored project differs from this bundle; pass --upgrade to replace it"}
    elif not active and _holds_data(conn) and not upgrade:
        raise BundleError("this database already holds source data loaded without a project; initialise a fresh "
                          "database, or pass --upgrade to adopt this bundle for the data it holds")
    status = "upgraded" if stored is not None else "initialised"
    projects.save(conn, name, cfg, by)  # a project without a schema of its own is the database's first
    clear_cache()
    settings._first.clear()
    return {**out, "status": status}


def schema_for(name: str) -> str:
    return "proj_" + re.sub(r"[^a-z0-9_]", "_", name)


def add(conn, b: dict, *, by: str, upgrade: bool, out: dict, stored: dict | None) -> dict:
    """Another project in this instance: its own schema (proj_<name>) holds its register, beside the first
    project's; accounts, access and settings stay shared. Its schema starts empty, with no data loaded."""
    from . import db

    name, cfg = b["name"], {**b["config"], "schema": schema_for(b["name"])}
    out = {**out, "schema": cfg["schema"]}
    if stored is not None:
        if stored.get("bundle", {}).get("digest") == cfg["bundle"]["digest"]:
            return {**out, "status": "unchanged"}
        if not upgrade:
            return {**out, "status": "kept", "stored_bundle": stored.get("bundle", {}).get("digest"),
                    "note": "the stored project differs from this bundle; pass --upgrade to replace it"}
        cfg["schema"] = stored.get("schema") or cfg["schema"]  # an upgrade keeps the register where it is
    db.create_project_schema(conn, cfg["schema"])
    projects.save(conn, name, cfg, by)
    clear_cache()
    return {**out, "status": "upgraded" if stored is not None else "added"}

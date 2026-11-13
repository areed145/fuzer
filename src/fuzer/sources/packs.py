"""The source pack contract (P1.5): what one system type brings to Fuzer, and the conformance every pack passes.

A source pack is one YAML file (`fuzer_source_pack: 1`, see `_catalog_packs` for its full layout):

    system:           the system type: name, kind, owner, timezone, date format, webhook secret reference
    connections:      how Fuzer reaches it: a connection template (secrets as references, never values)
    datasets:         what it exports, how each loads, the mapping in use and its quality checks
    exception_types:  the work types it raises, each with the role that owns it (they appear on Routing)
    exception_rules:  when a record raises that work
    detection_providers: an outside detection tool's alerts mapped to findings, through POST /hooks/{system}/alerts
                      (P3.8, providers.py)
    detectors:        the registered detectors it brings, switched on when the pack is installed (Detection)
    fixture:          a folder of sample exports, relative to the pack file: the fake source the conformance
                      suite loads, and what a project without the real system can try it on

    optional:         datasets not every site exports: loaded when the fixture has them

Write-back adapters, where a system accepts one, join the contract with the write-back queue (P5.7).

The built-in sources are packs too (`src/fuzer/source_packs/<system>.yaml`): each names its datasets, and `builtin`
assembles the full pack from the built-in mapping files and readers, so a mapping lives in one place. Their
fixtures are a bundle's exports: `conformance(..., fixture=<exports>)`.

`install` puts a pack on a project in one step: its datasets on Sources, its work types on Routing and its
detectors on Detection. `conformance` is the suite every pack must pass, against a database it may write to:
each connection declares its class and direction (D4, docs/ot-security.md: a control or control-adjacent one only
reads), the pack installs, installs again with nothing changed, its fixture loads through its own mappings with no
dataset held, failed or missing, and each detector it names is registered.
"""

from pathlib import Path

import yaml

from ._catalog_common import CatalogError

VERSION = 1


class PackError(CatalogError):
    pass


def read(path) -> tuple[str, dict, Path | None]:
    """A pack file: its text, the parsed pack and its fixture folder (None without one)."""
    path = Path(path)
    if not path.is_file():
        raise PackError(f"no source pack at {path}")
    text = path.read_text()
    try:
        pack = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PackError(f"{path} isn't YAML: {e}") from None
    if not isinstance(pack, dict) or pack.get("fuzer_source_pack") != VERSION:
        raise PackError(f"{path} is not a source pack (fuzer_source_pack: {VERSION})")
    fixture = pack.get("fixture")
    return text, pack, (path.parent / fixture).resolve() if fixture else None


def builtin_names() -> list[str]:
    from ..resources import SOURCE_PACKS

    return sorted(p.stem for p in SOURCE_PACKS.glob("*.yaml"))


def builtin(name: str) -> str:
    """A built-in source pack, assembled from its manifest, the built-in mapping files and the readers."""
    from ..resources import SOURCE_PACKS
    from . import _catalog_io
    from ._catalog_common import CATALOG, SYSTEM_KINDS, WHERE
    from .ingest import READERS

    path = SOURCE_PACKS / f"{name}.yaml"
    if not path.is_file():
        raise PackError(f"no built-in source pack {name!r} (built in: {', '.join(builtin_names())})")
    manifest = yaml.safe_load(path.read_text())
    system = manifest["system"]["name"]
    files = _catalog_io.seed_files(lambda: [_catalog_io.DEFAULT_DIR])
    by_dataset = {raw["dataset"]: (raw, text) for raw, text in files if raw.get("dataset")}
    by_type = {(raw["source"], raw["record_type"]): text for raw, text in files}
    datasets = []
    for ds in manifest["datasets"]:
        if ds in READERS:
            r = READERS[ds]
            datasets.append({"name": ds, "reader": ds, "mappings": [
                {"source": r.source, "record_type": rt, "yaml": by_type[(r.source, rt)]}
                for rt in r.record_types if (r.source, rt) in by_type]})
            continue
        if ds not in by_dataset:
            raise PackError(f"built-in pack {name}: no built-in mapping or reader for dataset {ds!r}")
        raw, text = by_dataset[ds]
        item = {"name": ds, "record_type": raw["record_type"]}
        for k in WHERE:
            if raw.get(k) is not None:
                item[k] = [raw[k]] if k == "key" and isinstance(raw[k], str) else raw[k]
        item["load_mode"] = "append" if raw.get("watermark") else "snapshot"
        item |= {k: v for k, v in (raw.get("catalog") or {}).items() if k in CATALOG}
        item["mappings"] = [{"source": raw["source"], "record_type": raw["record_type"], "yaml": text}]
        datasets.append(item)
    pack = {"fuzer_source_pack": VERSION,
            "system": {"name": system, **({"kind": SYSTEM_KINDS[system]} if system in SYSTEM_KINDS else {}),
                       **{k: v for k, v in manifest["system"].items() if k != "name"}},
            "datasets": datasets}
    for k in ("optional", "detectors", "exception_types", "exception_rules"):
        if manifest.get(k):
            pack[k] = manifest[k]
    return yaml.safe_dump(pack, sort_keys=False, allow_unicode=True, width=110)


def detectors_of(pack: dict) -> list[str]:
    """The detectors a pack names, each checked against the registry."""
    from ..core import detectors

    names = pack.get("detectors") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise PackError("detectors must be a list of registered detector names")
    known = detectors.registry()
    unknown = [n for n in names if n not in known]
    if unknown:
        raise PackError(f"the pack names detectors nobody registered: {', '.join(unknown)}")
    return names


def install(conn, text: str, by: str, reason: str | None = None, config: dict | None = None, data_dir=None,
            project_name: str | None = None) -> dict:
    """Install a pack on the project in one step: system, connections, datasets, mappings and checks (Sources),
    work types and rules (Routing), and its detectors switched on (Detection). Safe to repeat."""
    from ..core import detectors
    from . import _catalog_packs

    pack = yaml.safe_load(text or "") or {}
    names = detectors_of(pack) if isinstance(pack, dict) else []  # every name known before anything is written
    report = _catalog_packs.import_pack(conn, text, by, reason, config=config, data_dir=data_dir)
    switched = []
    for name in names:
        if detectors.on(conn, name, project_name):
            report.setdefault("unchanged", []).append(f"detector {name}")
            continue
        detectors.switch(conn, name, True, by=by, reason=reason or "installed with its source pack",
                         project_name=project_name)
        switched.append(name)
    if switched:
        report["detectors"] = switched
    return report


def conformance(conn, path, *, fixture=None, by: str = "conformance") -> dict:
    """Run the conformance suite for a pack on this connection's project: the pack at `path`, or a built-in pack
    by name, loading `fixture` (a folder of exports) instead of the pack's own. Returns
    {pack, system, problems, installed, loaded, absent}; a pack conforms when `problems` is empty."""
    from . import ingest

    problems: list[str] = []
    try:
        if isinstance(path, str) and path in builtin_names():
            text = builtin(path)
            pack, own = yaml.safe_load(text), None
        else:
            text, pack, own = read(path)
    except PackError as e:
        return {"pack": str(path), "system": None, "problems": [str(e)], "installed": {}, "loaded": {}}
    fixture = Path(fixture).resolve() if fixture is not None else own
    system = (pack.get("system") or {}).get("name")
    out = {"pack": str(path), "system": system, "problems": problems, "installed": {}, "loaded": {}, "absent": []}
    datasets = [d for d in pack.get("datasets") or [] if isinstance(d, dict)]
    pushes = [p for p in pack.get("detection_providers") or [] if isinstance(p, dict)]
    if not datasets and not pushes:
        problems.append("the pack brings no datasets and no detection provider")
    for d in datasets:
        if not d.get("mappings") and not d.get("reader"):
            problems.append(f"dataset {d.get('name')!r} has no mapping or reader")
    from ._catalog_common import check_zone

    for c in pack.get("connections") or []:  # D4: each connection says what zone it reaches into and which way
        if not isinstance(c, dict) or not c.get("class") or not c.get("direction"):
            problems.append(f"connection {c.get('name') if isinstance(c, dict) else c!r} doesn't declare its class "
                            "(control, control_adjacent or business) and direction (read or write)")
            continue
        try:
            check_zone(c["class"], c["direction"])
        except CatalogError as e:
            problems.append(f"connection {c.get('name')!r}: {e}")
    named = {d.get("name") for d in datasets}
    for r in pack.get("exception_rules") or []:
        if isinstance(r, dict) and r.get("dataset") not in named:
            problems.append(f"rule {r.get('name')!r} reads dataset {r.get('dataset')!r}, which the pack doesn't bring")
    if fixture is None:
        problems.append("the pack has no fixture: a folder of sample exports to load")
    elif not fixture.is_dir():
        problems.append(f"its fixture folder {fixture} is missing")
    if problems:
        return out
    config = {c["name"]: {"path": str(fixture)} for c in pack.get("connections") or []
              if isinstance(c, dict) and c.get("type") == "folder"}
    try:
        out["installed"] = install(conn, text, by, config=config, data_dir=fixture)
        again = install(conn, text, by, config=config, data_dir=fixture)
    except CatalogError as e:
        problems.append(f"it doesn't install: {e}")
        return out
    if set(again) - {"unchanged"}:
        problems.append(f"installing it again changed {sorted(set(again) - {'unchanged'})}")
    if pushes:
        out["alerts"] = _sample_alerts(conn, system, pushes, fixture, problems)
    if not datasets:
        return out
    optional = set(pack.get("optional") or [])
    out["absent"] = sorted(n for n in optional & named if ingest.dataset(fixture, n, conn) is None)
    wanted = sorted(named - set(out["absent"]))
    if not wanted:
        problems.append("its fixture has none of its datasets")
        return out
    try:
        loaded = ingest.ingest(conn, fixture, exports=wanted, trigger="api")
    except Exception as e:  # a failed load is recorded on the dataset's runs; here it is a problem with the pack
        conn.rollback()
        problems.append(f"its fixture doesn't load: {type(e).__name__}: {e}")
        return out
    out["loaded"] = {k: loaded.get(k) for k in ("loaded", "held", "missing", "records")}
    for outcome in ("held", "missing"):
        if loaded.get(outcome):
            problems.append(f"its fixture's datasets {outcome}: {', '.join(map(str, loaded[outcome]))}")
    if not loaded.get("records"):
        problems.append("its fixture loaded no records")
    return out


def _sample_alerts(conn, system: str, pushes: list[dict], fixture: Path, problems: list[str]) -> dict:
    """A provider's fixture is a sample of its alerts: a polled one's table (`poll`), else `<provider>.json` or
    `alerts.json` (dates relative to today as the bundles write them). Each must read through its mapping."""
    import json

    from . import connectors, providers
    from .relative_dates import resolve_all

    out = {}
    for p in pushes:
        if p.get("poll"):
            table = connectors.find(fixture, p["poll"])
            if table is None:
                problems.append(f"provider {p['name']}: its fixture has no {p['poll']} table to poll")
                continue
            res = providers.alerts(conn, system, list(table.rows()), p["name"])
            problems.extend(f"provider {p['name']}: {e}" for e in res["errors"])
            out[p["name"]] = len(res["alerts"])
            continue
        sample = next((f for f in (fixture / f"{p['name']}.json", fixture / "alerts.json") if f.is_file()), None)
        if sample is None:
            problems.append(f"provider {p['name']}: its fixture has no {p['name']}.json or alerts.json of sample alerts")
            continue
        res = providers.alerts(conn, system, resolve_all(json.loads(sample.read_text())), p["name"])
        for e in res["errors"]:
            problems.append(f"provider {p['name']}: {e}")
        out[p["name"]] = len(res["alerts"])
    return out

"""Asset packs: everything that differs between assets, loaded from YAML."""

import os
import time
from dataclasses import dataclass, field

from .tags import PatternWellGrammar, TagGrammar, WellGrammar


@dataclass
class Pack:
    name: str
    facility: str
    tags: TagGrammar
    wells: WellGrammar | PatternWellGrammar
    aliases: dict[str, tuple[str, str]]
    matching: dict
    survivorship: dict
    classification: dict
    maintenance_codes: dict
    downtime_reasons: dict
    spatial_links: list
    value: dict
    limits: dict
    reference_data: dict = field(default_factory=dict)
    data_notice: str | None = None  # how this asset's data must be labeled wherever it's shown
    investigation: dict = field(default_factory=dict)  # the guided investigation, when the bundle has one

    def rule(self, field: str) -> dict:
        return {**self.survivorship["default"], **self.survivorship.get("fields", {}).get(field, {})}


NO_PROJECT = ("No project to serve: this database holds none and FUZER_PROJECT isn't set. Initialise it from a seed "
              "bundle: `fuzer init --bundle <folder>`")
CACHE_SECONDS = 5.0


def pack_from_config(raw: dict) -> Pack:
    """A Pack from its YAML/JSON form (a seed bundle's pack.yaml, or the copy stored in its project)."""
    tg = raw["tag_grammar"]
    return Pack(
        name=raw["name"],
        facility=raw["facility"],
        tags=TagGrammar(tg["function_codes"], tg.get("area_digits", 2), tuple(tg.get("loop_digits", (3, 5))),
                        area=tg.get("area"), pattern=tg.get("pattern")),
        wells=(PatternWellGrammar(wg["pattern"], wg["key"]) if "pattern" in (wg := raw["well_grammar"])
               else WellGrammar(wg["platforms"])),
        aliases={phrase: tuple(target) for phrase, target in raw.get("aliases", {}).items()},
        matching=raw["matching"],
        survivorship=raw["survivorship"],
        classification=raw.get("classification", {}),
        maintenance_codes=raw.get("maintenance_codes", {}),
        downtime_reasons=raw.get("downtime_reasons", {}),
        spatial_links=raw.get("spatial_links", []),
        value=raw.get("value", {}),
        limits=raw.get("limits", {}),
        data_notice=raw.get("data_notice"),
        investigation=raw.get("investigation") or {},
    )


def load_pack(name: str) -> Pack:
    """The pack of a shipped seed bundle, by bundle name (fakeplant/bundles/<name>), or of the bundle at a path.
    Fuzer packages no packs of its own (P1.6). Code serving requests uses `current_pack`, which follows the
    project it runs for."""
    from . import bundles

    path = bundles.find(name)
    if path is None:
        raise LookupError(f"no seed bundle {name!r}; a seed bundle's pack is stored in its project")
    return bundles.read(path)["pack"]


def for_project(cfg: dict) -> Pack:
    """The pack a project config selects: the pack it carries (from a seed bundle), else a shipped bundle's pack by name."""
    if cfg.get("pack_config"):
        return pack_from_config(cfg["pack_config"])
    return load_pack(cfg.get("pack") or cfg["name"])


_parsed: dict[str, Pack] = {}  # config fingerprint -> Pack, so a request doesn't re-parse YAML
_current: dict[str | None, tuple[float, Pack]] = {}  # database URL -> (when, pack)


def _from_config(cfg: dict) -> Pack:
    import hashlib
    import json

    fp = hashlib.sha256(json.dumps({k: v for k, v in cfg.items() if k != "source"}, sort_keys=True,
                                   default=str).encode()).hexdigest()
    if fp not in _parsed:
        _parsed[fp] = for_project(cfg)
    return _parsed[fp]


def resolve(conn) -> Pack:
    """The pack of the project this code serves (#376), read now: the request's or job's project, else FUZER_PROJECT,
    else the database's first project (`fuzer init --bundle`). No project is an error, not a default."""
    from . import projects

    name = projects.current(conn)
    if not name:
        raise projects.ProjectError(NO_PROJECT)
    return _from_config(projects.config(conn, name))


def current_pack(conn=None) -> Pack:
    """The pack this process serves, following its database at runtime rather than fixed at import. Cached per
    database for a few seconds; a changed project config gives a different pack on the next read. `conn` None
    opens a short connection (without a configured database, FUZER_PROJECT's repository config)."""
    from . import db

    url = os.environ.get("FUZER_DATABASE_URL") or (db._server.get_uri() if db._server is not None else None)
    key = (url, db.scope(), os.environ.get("FUZER_PROJECT"))
    hit = _current.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if conn is not None:
        pack = resolve(conn)
    elif url is None:
        from . import projects

        name = projects.current(None)
        if not name:
            raise projects.ProjectError(NO_PROJECT)
        pack = _from_config(projects.config(None, name))
    else:
        with db.connect() as c:
            pack = resolve(c)
    _current[key] = (time.monotonic(), pack)
    return pack


def clear_cache() -> None:
    _current.clear()

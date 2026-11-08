"""Projects (#131): one name picks the asset pack, data folder, mappings, time-series provider, timezone and
cache, instead of an environment variable for each.

A project's config is the `project` row in the database; `projects/<name>.yaml` in the repository is the
fallback (a fresh database, CI, the demos) and seeds the row with `fuzer projects save <name>`. Paths are
relative to the repository.

    fuzer --project okan eval --ops --full        # or FUZER_PROJECT=okan

For one release, activating a project fills in the old variables (FUZER_DATA_DIR, FUZER_MAPPINGS,
FUZER_TIMESERIES, FUZER_TIMEZONE, FUZER_CACHE_DIR) from it, so code that still reads them works; a variable
already set in the environment wins, with a warning.
"""

import os
import warnings
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from ..resources import ROOT

REPO = ROOT / "projects"
FIELDS = {"data": "FUZER_DATA_DIR", "mappings": "FUZER_MAPPINGS",
          "timeseries": "FUZER_TIMESERIES", "timezone": "FUZER_TIMEZONE", "cache": "FUZER_CACHE_DIR"}


class ProjectError(LookupError):
    pass


def current(conn=None) -> str | None:
    """The project this code serves: the request's or job's (`use`), else FUZER_PROJECT when the process was started
    for one, else the database's first project (set by `fuzer init --bundle`), else None. `conn` None reads only
    the scope and the process's choice."""
    from . import db

    return db.scope().project or os.environ.get("FUZER_PROJECT") or (active(conn) if conn is not None else None)


def schema_of(cfg: dict) -> str:
    """The schema holding a project's register: `public` for a database's first project, else proj_<name>."""
    from . import db

    return cfg.get("schema") or db.PROJECT_SCHEMA


def scope(conn, name: str):
    """The connection scope for project `name` (ProjectError if there's no such project)."""
    from . import db

    return db.Scope(name, schema_of(config(conn, name)))


def use(conn, name: str):
    """`with projects.use(conn, "kestrel"):` runs the block, and every connection it opens, for that project."""
    from . import db

    return db.use(scope(conn, name))


def active(conn) -> str | None:
    """The database's first project: the stored project whose register is `public` (the first one a bundle
    initialised), or None. Every other project is added beside it in a schema of its own."""
    from . import db

    first = [name for name, cfg in _db_rows(conn).items() if (cfg.get("schema") or db.PROJECT_SCHEMA) == db.PROJECT_SCHEMA]
    return min(first) if first else None


def repo_names() -> list[str]:
    return sorted(p.stem for p in REPO.glob("*.yaml")) if REPO.is_dir() else []


def repo_config(name: str) -> dict | None:
    p = REPO / f"{name}.yaml"
    return yaml.safe_load(p.read_text()) or {} if p.is_file() else None


def _db_rows(conn) -> dict[str, dict]:
    if conn is None:
        return {}
    try:
        return {r["name"]: r["config"] for r in conn.execute("select name, config from project")}
    except Exception:  # a database from before #131: no project table yet
        conn.rollback()
        return {}


def stored(conn) -> list[str]:
    """The projects this database holds (initialised from a bundle), not repository files."""
    return sorted(_db_rows(conn))


def names(conn=None) -> list[str]:
    return sorted(set(_db_rows(conn)) | set(repo_names()))


def config(conn, name: str) -> dict:
    """The database row, else the repository file; ProjectError if neither."""
    row = _db_rows(conn).get(name)
    cfg = row if row is not None else repo_config(name)
    if cfg is None:
        known = ", ".join(names(conn)) or "none"
        raise ProjectError(f"no project {name!r} (known: {known})")
    return {"name": name, "pack": name, "cache": f".fuzer/cache/{name}", **cfg,
            "source": "database" if row is not None else "repository"}


def _path(v: str) -> str:
    p = Path(v)
    return str(p if p.is_absolute() else ROOT / p)


_fields: dict[tuple, tuple[float, dict]] = {}
FIELD_SECONDS = 5.0


def field(name: str) -> str | None:
    """A project setting with an old process-wide variable (data, mappings, timeseries, timezone, cache) for the
    project this code runs for. A project added beside the first takes its own config, whatever the process's
    environment says, so one deployment serves several projects; the first project keeps the variable, then its
    config. Paths come back absolute."""
    import time

    from . import db

    var = FIELDS[name]
    s = db.scope()
    if s.project and s.schema != db.PROJECT_SCHEMA:
        key = (os.environ.get("FUZER_DATABASE_URL") or (db._server.get_uri() if db._server else None), s)
        hit = _fields.get(key)
        if not hit or time.monotonic() - hit[0] >= FIELD_SECONDS:
            with db.connect() as conn:
                hit = _fields[key] = (time.monotonic(), config(conn, s.project))
        value = hit[1].get(name)
        if value is not None:
            return _path(value) if name in ("data", "mappings", "cache") else str(value)
        return None
    return os.environ.get(var) or None


def activate(conn, name: str) -> dict:
    """Make `name` this process's project: FUZER_PROJECT, and the old path/provider variables from its config.
    `pack.current_pack` reads the pack from the project at runtime (#376)."""
    from . import db

    cfg = config(conn, name)
    os.environ["FUZER_PROJECT"] = name
    db.set_default(db.Scope(name, schema_of(cfg)))  # this process's connections search the project's schema
    if schema_of(cfg) != db.PROJECT_SCHEMA:
        return cfg  # an added project's settings come from its config (`field`), never the process's variables
    for field, var in FIELDS.items():
        if field == "pack" or cfg.get(field) is None:
            continue
        value = _path(cfg[field]) if field in ("data", "mappings", "cache") else str(cfg[field])
        if os.environ.get(var) and os.environ[var] != value:
            warnings.warn(f"{var} is set ({os.environ[var]}) and overrides project {name}'s {field} ({value}); "
                          f"set it in the project instead (#131)", stacklevel=2)
            continue
        os.environ[var] = value
    return cfg


def save(conn, name: str, cfg: dict, by: str) -> dict:
    """Store a project's config in the database (it then wins over the repository file)."""
    cfg = {k: v for k, v in cfg.items() if k not in ("name", "source")}
    conn.execute(
        """
        insert into project (name, config, updated_by) values (%s, %s, %s)
        on conflict (name) do update set config = excluded.config, updated_by = excluded.updated_by, updated_at = now()
        """, (name, Jsonb(cfg), by))
    conn.commit()
    return config(conn, name)

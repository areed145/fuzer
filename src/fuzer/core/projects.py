"""Projects (#131): one name picks the asset pack, data folder, mappings, time-series provider, timezone and
cache, instead of an environment variable for each.

A project's config is the `project` row in the database, stored by `fuzer init --bundle`. A shipped seed bundle of
the same name (fakeplant/bundles/<name>) is the fallback when the database has no row (CI, the demos); Fuzer keeps
no project files of its own (P1.6).

    fuzer --project <name> eval --ops --full      # or FUZER_PROJECT=<name>

Activating a project changes no environment variable (P1.6): code asks `field()` for the project's data folder,
mappings, time-series provider, timezone and cache, which reads them from the project. The old variables
(FUZER_DATA_DIR, FUZER_MAPPINGS, FUZER_TIMESERIES, FUZER_TIMEZONE, FUZER_CACHE_DIR) are deprecated: set in the
environment they still override the database's first project, with a warning, and never a project added beside it.
"""

import os
import warnings
from pathlib import Path

from psycopg.types.json import Jsonb

from ..resources import ROOT

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
    """`with projects.use(conn, name):` runs the block, and every connection it opens, for that project."""
    from . import db

    return db.use(scope(conn, name))


def active(conn) -> str | None:
    """The database's first project: the stored project whose register is `public` (the first one a bundle
    initialised), or None. Every other project is added beside it in a schema of its own."""
    from . import db

    first = [name for name, cfg in _db_rows(conn).items() if (cfg.get("schema") or db.PROJECT_SCHEMA) == db.PROJECT_SCHEMA]
    return min(first) if first else None


def repo_names() -> list[str]:
    """The shipped seed bundles, each a project a database can be initialised for."""
    from . import bundles

    return bundles.shipped()


def repo_config(name: str) -> dict | None:
    """A shipped bundle's project config (with its pack), or None."""
    from . import bundles

    path = bundles.find(name) if bundles.NAME.fullmatch(name or "") else None
    return bundles.read(path)["config"] if path is not None else None


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
    """The database row, else the shipped bundle's; ProjectError if neither."""
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


def _cached_config(project: str | None) -> dict | None:
    """A project's config (None: the database's first project), re-read every few seconds; None without one."""
    import time

    from . import db

    key = (os.environ.get("FUZER_DATABASE_URL") or (db._server.get_uri() if db._server else None), project)
    hit = _fields.get(key)
    if not hit or time.monotonic() - hit[0] >= FIELD_SECONDS:
        try:
            with db.connect() as conn:
                name = project or active(conn)
                cfg = config(conn, name) if name else None
        except Exception:  # no such project, or no database to ask: nothing configured
            cfg = None
        hit = _fields[key] = (time.monotonic(), cfg)
    return hit[1]


_warned: set[str] = set()


def field(name: str) -> str | None:
    """A project setting (data, mappings, timeseries, timezone, cache) of the project this code runs for, read
    from its config, so one deployment serves several projects without environment variables. The deprecated
    process-wide variable still overrides the database's first project (with a warning), never a project added
    beside it. Paths come back absolute."""
    from . import db

    var = FIELDS[name]
    s = db.scope()
    project = s.project or os.environ.get("FUZER_PROJECT")
    first = not s.project or s.schema == db.PROJECT_SCHEMA
    if first and os.environ.get(var):
        if project and var not in _warned:
            _warned.add(var)
            warnings.warn(f"{var} is deprecated and overrides project {project}'s {name}; set it in the project "
                          "instead (P1.6)", DeprecationWarning, stacklevel=2)
        return os.environ[var]
    cfg = _cached_config(project)
    value = (cfg or {}).get(name)
    if value is None:
        return None
    return _path(value) if name in ("data", "mappings", "cache") else str(value)


def activate(conn, name: str) -> dict:
    """Make `name` this process's project: its connections search the project's schema and `field()` reads the
    project's config. No environment variable is written (P1.6)."""
    from . import db

    cfg = config(conn, name)
    db.set_default(db.Scope(name, schema_of(cfg)))
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

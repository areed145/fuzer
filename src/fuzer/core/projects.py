"""Projects (#131): one name picks the asset pack, data folder, mappings, time-series provider, timezone and
cache, instead of an environment variable for each.

A project's config is the `project` row in the database; `projects/<name>.yaml` in the repository is the
fallback (a fresh database, CI, the demos) and seeds the row with `fuzer projects save <name>`. Paths are
relative to the repository.

    fuzer --project okan eval --ops --full        # or FUZER_PROJECT=okan

For one release, activating a project fills in the old variables (FUZER_PACK, FUZER_DATA_DIR, FUZER_MAPPINGS,
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
FIELDS = {"pack": "FUZER_PACK", "data": "FUZER_DATA_DIR", "mappings": "FUZER_MAPPINGS",
          "timeseries": "FUZER_TIMESERIES", "timezone": "FUZER_TIMEZONE", "cache": "FUZER_CACHE_DIR"}


class ProjectError(LookupError):
    pass


def current() -> str | None:
    return os.environ.get("FUZER_PROJECT") or None


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


def activate(conn, name: str) -> dict:
    """Make `name` the current project: FUZER_PROJECT, and the old variables from its config."""
    cfg = config(conn, name)
    os.environ["FUZER_PROJECT"] = name
    for field, var in FIELDS.items():
        if cfg.get(field) is None:
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

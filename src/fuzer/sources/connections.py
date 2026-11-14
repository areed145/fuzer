"""Connection types (#225): how Fuzer reaches a source system. Each type declares its config and secrets and
implements one contract, so a new live system (SAP OData, Maximo REST, SQL) plugs in here without touching
ingest:

    connector(dataset, data_dir)  the dataset's reader: fingerprint(), schema(), rows(since) (connectors.py)
    test(data_dir, rows)          reach it, authenticate, list what it holds, read a few rows: each step timed
    discover(data_dir)            the tables, files or feeds it can see

Secrets are references, never values: `env:NAME` reads an environment variable (a secret store later uses
the same form). Rotating a secret needs no config change; `resolves` says whether a reference finds a value.

Types: folder (a folder of exports: CSV, Parquet, Delta, Excel, JSON, GeoJSON; empty path = the project's data
folder), pi_web_api (PI points and the AF tree), adx (the ADX series catalog).
"""

import os
import time
from pathlib import Path

from psycopg.types.json import Jsonb


class ConnectionError_(ValueError):
    pass


def resolve(ref: str | None) -> str | None:
    """A secret reference's value: env:NAME reads the environment. None when it finds nothing."""
    if not ref:
        return None
    if ref.startswith("env:"):
        return os.environ.get(ref[4:]) or None
    raise ConnectionError_(f"secret references look like env:NAME, not {ref[:12]!r}...")


def resolves(secrets: dict) -> dict[str, bool]:
    return {k: resolve(v) is not None for k, v in (secrets or {}).items()}


class Type:
    name = ""
    config: dict[str, object] = {}  # field -> default (None: required)
    secrets: tuple[str, ...] = ()
    live = True

    def __init__(self, config: dict, secrets: dict):
        self.cfg = {**{k: v for k, v in self.config.items() if v is not None}, **(config or {})}
        self.secret_refs = secrets or {}

    def secret(self, name: str) -> str | None:
        return resolve(self.secret_refs.get(name))

    def connector(self, d: dict, data_dir: Path):
        raise NotImplementedError

    def discover(self, data_dir: Path) -> list[str]:
        raise NotImplementedError

    def test(self, data_dir: Path, rows: int = 5) -> list[dict]:
        raise NotImplementedError


# What a failing HTTP status means to the person testing a connection (R.2): the server's own words follow.
SAID = {401: "the server refused the credential (401): check the user and password or the token, which may have "
             "expired",
        403: "the credential works but may not read this (403): ask for read access",
        404: "not found on the server (404): check the URL, server and database names",
        429: "the server is throttling requests (429): wait and test again; Fuzer does not retry on its own",
        503: "the server is busy or down (503): wait and test again; Fuzer does not retry on its own",
        504: "the server timed out (504): test again with fewer rows, or ask whether it is under load"}


def plain(e: Exception) -> str:
    """A failure in plain words, for `fuzer sources test`: what went wrong and what to do, then what the server
    said."""
    import httpx

    status = getattr(e, "status", None) or (e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None)
    said = ""
    if isinstance(e, httpx.HTTPStatusError):
        try:
            body = e.response.json()
            said = "; ".join(body.get("Errors") or []) if isinstance(body, dict) else ""
        except ValueError:
            said = ""
    elif status is not None:
        said = str(e)
    if status in SAID:
        return SAID[status] + (f". The server said: {said}" if said else "")
    if status:
        return f"the server answered {status}" + (f": {said}" if said else "")
    if isinstance(e, httpx.ConnectError):
        return f"could not reach the server: {e}"
    if isinstance(e, httpx.TimeoutException):
        return "the server did not answer in time: check the network path to it"
    return f"{type(e).__name__}: {e}"


def _step(steps: list, name: str, fn):
    t = time.perf_counter()
    try:
        detail = fn()
        steps.append({"step": name, "ok": True, "ms": round((time.perf_counter() - t) * 1000), "detail": detail})
        return detail
    except Exception as e:  # each step reports its own failure; later ones are skipped
        steps.append({"step": name, "ok": False, "ms": round((time.perf_counter() - t) * 1000), "detail": plain(e)})
        raise _Stop from e


class _Stop(Exception):
    pass


class Folder(Type):
    name = "folder"
    config = {"path": ""}
    live = False

    def root(self, data_dir: Path) -> Path:
        """Where the folder is: an absolute path; a relative one in the data folder, else the repository, else the
        folder Fuzer runs from (the first that exists); no path: the data folder."""
        p = self.cfg.get("path") or ""
        if not p:
            return Path(data_dir)
        if Path(p).is_absolute():
            return Path(p)
        from ..resources import ROOT

        tried = [Path(data_dir) / p, ROOT / p, Path.cwd() / p]
        return next((t for t in tried if t.is_dir()), tried[0])

    def check_path(self, data_dir: Path) -> None:
        root = self.root(data_dir)
        if not root.is_dir():
            from ..resources import ROOT

            raise ConnectionError_(f"no folder {root}: give an absolute path, or one relative to the data folder "
                                   f"({data_dir}) or the repository ({ROOT})")

    def connector(self, d: dict, data_dir: Path):
        from . import connectors
        from .ingest import READERS, _Manifest

        root = self.root(data_dir)
        if (connectors.is_table_folder(root) or connectors.is_document_folder(root)) and not d.get("reader"):
            return connectors.at(root, d.get("connector"))  # the path is the table (or folder of documents) itself
        if d.get("reader"):
            r = READERS[d["reader"]]
            f = root / (d.get("location") or r.file)
            return _Manifest(f, r.ref, r.load) if f.is_file() else None
        if d.get("location"):
            return connectors.at(root / d["location"], d.get("connector"))  # an absolute location wins
        c = connectors.find(root, d["name"])
        if c is not None and d.get("connector") and c.kind != d["connector"]:
            return None  # the file there isn't the kind the dataset names
        return c

    def discover(self, data_dir: Path) -> list[str]:
        from . import connectors
        from .ingest import READERS

        self.check_path(data_dir)
        root = self.root(data_dir)
        if connectors.is_table_folder(root) or connectors.is_document_folder(root):
            return [root.name]  # the path is one table (a Delta or Parquet folder, or a folder of documents)
        return sorted(set(connectors.datasets(root)) | {n for n, r in READERS.items() if (root / r.file).is_file()})

    def test(self, data_dir: Path, rows: int = 5) -> list[dict]:
        from . import connectors

        steps: list[dict] = []
        root = self.root(data_dir)
        def reach():
            if not root.is_dir():
                raise FileNotFoundError(f"no folder {root}")
            return str(root)

        try:
            _step(steps, "reach", reach)
            found = _step(steps, "list", lambda: self.discover(data_dir))
            if connectors.is_table_folder(root) or connectors.is_document_folder(root):
                _step(steps, "read", lambda: {"dataset": root.name, "rows": [
                    r for _, r in zip(range(rows), connectors.at(root).rows())]})
            elif tables := [n for n in found if connectors.find(root, n) is not None]:
                _step(steps, "read", lambda: {"dataset": tables[0], "rows": [
                    r for _, r in zip(range(rows), connectors.find(root, tables[0]).rows())]})
        except _Stop:
            pass
        return steps


class PiWebApi(Type):
    name = "pi_web_api"
    config = {"url": None, "data_server": "", "af_server": "", "af_database": "", "user": "", "verify_tls": True}
    secrets = ("password",)
    FEEDS = {"pi_points": "PiPoints", "points": "PiPoints", "pi_af": "PiAf", "af": "PiAf"}

    def client(self):
        from . import pi_web_api

        return pi_web_api.Client(cfg={**self.cfg, "password": self.secret("password")})

    def connector(self, d: dict, data_dir: Path):
        from . import pi_web_api

        feed = self.FEEDS.get(d.get("location") or d.get("reader") or d["name"])
        if feed is None:
            raise ConnectionError_(f"a PI Web API dataset is pi_points or pi_af (location), not {d['name']!r}")
        return getattr(pi_web_api, feed)(self.client())

    def discover(self, data_dir: Path) -> list[str]:
        return ["pi_points", "pi_af"]

    def test(self, data_dir: Path, rows: int = 5) -> list[dict]:
        steps: list[dict] = []
        try:
            c = _step(steps, "reach", lambda: self.client())
            server = _step(steps, "authenticate", lambda: c.data_server())
            _step(steps, "list", lambda: {"data_server": server["Name"], "af_database": c.af_database()["Name"]})
            _step(steps, "read", lambda: {"rows": [p["Name"] for _, p in zip(range(rows), c.items(
                f"dataservers/{server['WebId']}/points"))]})
        except _Stop:
            pass
        return steps


class Adx(Type):
    name = "adx"
    config = {"url": None, "database": "", "catalog": "SeriesCatalog"}
    secrets = ("token",)

    def client(self):
        from . import adx

        return adx.Client(cfg={**self.cfg, "token": self.secret("token")})

    def connector(self, d: dict, data_dir: Path):
        from . import adx

        return adx.SeriesCatalog(self.client(), catalog=self.cfg.get("catalog"))

    def discover(self, data_dir: Path) -> list[str]:
        """The catalog table this connection reads. Listing every table needs `.show tables`, a management command,
        and the connector never calls the management endpoint (R.2b)."""
        return [self.cfg.get("catalog") or "SeriesCatalog"] if self.cfg.get("url") else []

    def test(self, data_dir: Path, rows: int = 5) -> list[dict]:
        from . import adx

        steps: list[dict] = []
        try:
            c = _step(steps, "reach", lambda: self.client())
            cat = self.cfg.get("catalog") or "SeriesCatalog"
            if not adx.IDENT.match(cat):
                raise ConnectionError_(f"bad catalog table name {cat!r}")
            # The cheapest query there is, so a refused token fails here and not at the read.
            _step(steps, "authenticate", lambda: {"database": c.database, "rows": c.query("print ok = 1")})
            _step(steps, "read", lambda: {"rows": c.query(f"{cat} | take {int(rows)}")})
        except _Stop:
            pass
        return steps


TYPES: dict[str, type[Type]] = {t.name: t for t in (Folder, PiWebApi, Adx)}


def check(type_: str, config: dict, secrets: dict) -> None:
    """Raise ValueError unless the type is known, its config has what it needs and secrets are references."""
    from .catalog import CatalogError

    t = TYPES.get(type_)
    if t is None:
        raise CatalogError(f"unknown connection type {type_!r}; one of {sorted(TYPES)}")
    unknown = set(config or {}) - set(t.config)
    if unknown:
        raise CatalogError(f"{type_} has no config {sorted(unknown)}; it takes {sorted(t.config)}")
    missing = [k for k, v in t.config.items() if v is None and not (config or {}).get(k)]
    if missing:
        raise CatalogError(f"{type_} needs {missing}")
    for k, v in (secrets or {}).items():
        if k not in t.secrets:
            raise CatalogError(f"{type_} has no secret {k!r}; it takes {list(t.secrets)}")
        if not (isinstance(v, str) and v.startswith("env:") and len(v) > 4):
            raise CatalogError(f"secret {k!r} must be a reference such as env:NAME, never the value")


def display(row: dict, data_dir=None) -> str:
    """A connection in words: the project data folder (and where it is), a folder path, a server URL."""
    cfg = row.get("config") or {}
    if row.get("type") == "folder":
        if not cfg.get("path"):
            return f"project data folder ({data_dir})" if data_dir else "project data folder"
        p = Path(cfg["path"])
        return str(p if p.is_absolute() or not data_dir else Path(data_dir) / p)
    return cfg.get("url") or row.get("type", "")


def open_(row: dict) -> Type:
    return TYPES[row["type"]](row.get("config") or {}, row.get("secrets") or {})


def connector(d: dict, data_dir) -> object | None:
    """The reader for a catalog dataset (as catalog.datasets returns it) through its connection; a CSV is read
    in its system's encoding."""
    row = {"type": d.get("connection_type") or "folder", "config": d.get("connection_config") or {},
           "secrets": d.get("connection_secrets") or {}}
    c = open_(row).connector(d, Path(data_dir))
    if c is not None and getattr(c, "kind", None) == "csv" and d.get("system_encoding"):
        c.encoding = d["system_encoding"]
    return c


def test(conn, connection_id: int, data_dir, rows: int = 5) -> dict:
    """Test a connection step by step; the outcome is kept on the connection (last_test)."""
    from . import catalog

    c = catalog.connection(conn, connection_id)
    if not c:
        raise catalog.CatalogError(f"no connection {connection_id}")
    steps = open_(c).test(Path(data_dir), rows)
    out = {"connection": c["name"], "system": c["system"], "type": c["type"], "ok": bool(steps) and all(s["ok"] for s in steps),
           "steps": steps, "secrets": resolves(c["secrets"])}
    from ..core.jsonsafe import plain

    conn.execute("update source_connection set last_tested_at = now(), last_test = %s where id = %s",
                 (Jsonb(plain(out)), connection_id))
    return out


def discover(conn, connection_id: int, data_dir) -> dict:
    """What a connection can see, and which of it no dataset covers yet (each can become a draft dataset)."""
    from . import catalog

    c = catalog.connection(conn, connection_id)
    if not c:
        raise catalog.CatalogError(f"no connection {connection_id}")
    from .onboard import _covered

    seen = open_(c).discover(Path(data_dir))
    covered = _covered(conn, c)
    return {"connection": c["name"], "system": c["system"], "found": seen, "uncovered": [n for n in seen if n not in covered]}


# ---- moving the old settings onto connections ----------------------------------------------------

LEGACY = {  # settings before #225 -> (connection type, systems and their datasets, config keys, secret key)
    "pi_web_api": ("sources.pi", {"pi": ["pi_points"], "pi_af": ["pi_af"]},
                   {"url": "url", "data_server": "data_server", "af_server": "af_server", "af_database": "af_database",
                    "user": "user", "verify_tls": "verify_tls"}, {"password": "password"}),
    "adx": ("sources.adx", {"adx": ["adx_series"]}, {"url": "url", "database": "database", "catalog": "catalog"},
            {"token": "token"}),
}


def seed(conn, by: str = "seed") -> list[str]:
    """A PI Web API or ADX server set in the old settings (sources.pi.*, sources.adx.*) becomes a connection on
    its systems, and their datasets move onto it. Idempotent: a server already on a connection is skipped."""
    from ..core import settings
    from . import catalog

    made = []
    for type_, (prefix, systems, cfg_keys, secret_keys) in LEGACY.items():
        if not (url := settings.get(conn, f"{prefix}.url")):
            continue
        cfg = {k: settings.get(conn, f"{prefix}.{s}") for k, s in cfg_keys.items()}
        sec = {k: settings.get(conn, f"{prefix}.{s}") for k, s in secret_keys.items()}
        sec = {k: v for k, v in sec.items() if v}
        for sys_name, names in systems.items():
            if not catalog.system(conn, sys_name):
                continue
            have = [c for c in catalog.connections(conn, sys_name) if c["type"] == type_ and c["config"].get("url") == url]
            c = have[0] if have else catalog.add_connection(conn, sys_name, type_, type_, by, cfg, sec,
                                                            reason=f"moved from the {prefix}.* settings (#225)")
            for n in names:
                d = catalog.dataset(conn, n)
                if d and d["connection_type"] in (None, "folder"):
                    catalog.update_dataset(conn, n, by, reason=f"moved to the {type_} connection", connection_id=c["id"])
            if not have:
                made.append(f"{sys_name}:{type_}")
    return made


def default_config(type_: str) -> dict | None:
    """The first enabled connection of a type, as config with its secrets resolved (the time-series providers
    read values through it); None without one."""
    from ..core import db

    if not os.environ.get("FUZER_DATABASE_URL") and db._server is None:
        return None
    try:
        with db.connect() as c:
            r = c.execute("select config, secrets from source_connection where type = %s and enabled order by id limit 1",
                          (type_,)).fetchone()
    except Exception:  # no database, or one from before #225
        return None
    if not r:
        return None
    return {**r["config"], **{k: resolve(v) for k, v in (r["secrets"] or {}).items()}}

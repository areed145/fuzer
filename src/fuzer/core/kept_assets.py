"""Built files from earlier deploys, kept so a page opened before a deploy keeps working (P5.18a, #1171).

A page asks for its built files by content-hashed name. After a deploy the new build has new names, so a page left
open (an iPad on the desk) asking for a file it hasn't loaded yet, such as the Gantt part of the diagram library,
got a 404 and showed the diagram's source. Each start copies the build's files, gzipped, into the deployment's
Postgres (table fuzer.kept_asset) and the server answers from there when the current build lacks a name. They used to
be kept on the persistent disk; the service has no disk now, so that Render can start the new instance before
stopping the old one (zero-downtime deploys), and Postgres is what both instances share.

    keep(conn, build, days)   copy in the build's files the table lacks, mark every file the build names as current,
                              and drop files no build has named for `days`
    fetch(conn, name)         a kept file's gzipped bytes, or None
    enabled()                 whether this deployment keeps files (FUZER_KEPT_ASSETS, set by the deploy runtime)
    Kept(directory=...)       the /assets mount: the build's files, then the kept ones

Files keep their content hash in the name, so a kept file is always the right one for the page that asks for it.
"""

import gzip
import mimetypes
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response

ENV = "FUZER_KEPT_ASSETS"
DAYS = 7  # longer than anyone keeps a tab open without reloading; ~15 deploys a day each add ~0.5 MB gzipped
TABLE = "fuzer.kept_asset"
CACHE = "public, max-age=31536000, immutable"  # a hashed name never changes content


def _ensure(conn) -> None:
    conn.execute("create schema if not exists fuzer")
    conn.execute(f"create table if not exists {TABLE} (name text primary key, body bytea not null, "
                 "last_named timestamptz not null default now())")


def keep(conn, build: Path, days: int = DAYS) -> dict:
    """Copy in the build's files the table lacks (gzipped) and mark every file the build names as current; drop kept
    files that no build has named for `days`."""
    _ensure(conn)
    files = {f.name: f for f in build.iterdir() if f.is_file()}
    have = {r["name"] for r in conn.execute(f"select name from {TABLE} where name = any(%s)",
                                            (list(files),)).fetchall()}
    copied = 0
    for name in sorted(set(files) - have):
        conn.execute(f"insert into {TABLE} (name, body) values (%s, %s) on conflict (name) do nothing",
                     (name, gzip.compress(files[name].read_bytes(), mtime=0)))
        copied += 1
    conn.execute(f"update {TABLE} set last_named = now() where name = any(%s)", (list(files),))
    dropped = conn.execute(f"delete from {TABLE} where not (name = any(%s)) and last_named < now() - make_interval("
                           "days => %s)", (list(files), days)).rowcount
    kept = conn.execute(f"select count(*) as n from {TABLE}").fetchone()["n"]
    conn.commit()
    return {"copied": copied, "kept": kept, "dropped": dropped}


def fetch(conn, name: str) -> bytes | None:
    row = conn.execute(f"select body from {TABLE} where name = %s", (name,)).fetchone()
    return bytes(row["body"]) if row else None


def enabled() -> bool:
    return bool(os.environ.get(ENV))


class Kept(StaticFiles):
    """The build's files; a name the build lacks is answered from the kept table when this deployment keeps files."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or not enabled() or "/" in path or path.startswith("."):
                raise
            from starlette.concurrency import run_in_threadpool

            body = await run_in_threadpool(_lookup, path)
            if body is None:
                raise
            accepts = any(k == b"accept-encoding" and b"gzip" in v for k, v in scope.get("headers", []))
            media = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if accepts:
                return Response(body, media_type=media, headers={"content-encoding": "gzip", "cache-control": CACHE,
                                                                 "vary": "Accept-Encoding"})
            return Response(gzip.decompress(body), media_type=media, headers={"cache-control": CACHE})


def _lookup(name: str) -> bytes | None:
    from . import db

    try:
        with db.connect() as conn:
            return fetch(conn, name)
    except Exception:
        return None  # no table yet, or the database unreachable: the page gets the 404 it would have had

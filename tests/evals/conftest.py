"""Kestrel stood up from its seed bundle in a fresh database, as a deployment would be, for evals that run on both
bundles. Okan comes from the shared `okan_db` template (tests/conftest.py)."""

from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"
KESTREL = BUNDLES / "kestrel"


@pytest.fixture(scope="module")
def kestrel_db(request):
    """A connection to a rebuilt Kestrel, one database per test module."""
    yield from _bundle_db(request, KESTREL)


@pytest.fixture(scope="module")
def harrier_db(request):
    """A connection to a rebuilt Harrier (the third bundle, a pipeline and terminal), one database per test module."""
    yield from _bundle_db(request, BUNDLES / "harrier")


def _bundle_db(request, bundle: Path):
    from fuzer.core import bundles, db, settings
    from fuzer.core import pack as packs
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    name = f"{bundle.name}_{request.module.__name__.rsplit('.', 1)[-1]}"
    admin = lambda: db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))
    with admin() as a:
        a.execute(f'drop database if exists "{name}" with (force)')
        a.execute(f'create database "{name}"')
    url = make_conninfo(db.database_url(), dbname=name)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATABASE_URL", url)
    mp.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, bundle, by="test")
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, packs.current_pack(conn))
            conn.commit()
            yield conn
    finally:
        mp.undo()
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        with admin() as a:
            a.execute(f'drop database if exists "{name}" with (force)')

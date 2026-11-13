"""Kestrel stood up from its seed bundle in a fresh database, as a deployment would be, for evals that run on both
bundles. Okan comes from the shared `okan_db` template (tests/conftest.py)."""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"
KESTREL = BUNDLES / "kestrel"
FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures"
OPTIONAL_ITEM = {"unit"}  # core.findings.Item: a unit is written only when the value has one


def _json(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else float(x) if isinstance(x, Decimal) else str(x)


def _shape(job: dict) -> dict:
    return {"keys": sorted(job), "finding": sorted(job["finding"]),
            "item": sorted({k for i in job["finding"]["items"] for k in i} - OPTIONAL_ITEM),
            "asset": sorted({k for a in job["assets"] for k in a})}


def field_jobs_path(bundle: str) -> Path:
    """Each field type's job as the loop eval gave it to the phone on `bundle`, which the browser test renders at
    390 px (ui/tests/browser.test.mjs). Okan's file kept its first name."""
    return FIXTURES / ("loop-field-jobs.json" if bundle == "okan" else f"loop-field-jobs-{bundle}.json")


@pytest.fixture
def field_jobs_kept():
    """A check that the loop run's field jobs on a bundle match the phone's fixture for it, type for type and in
    shape. FUZER_WRITE_FIXTURES=1 rewrites the fixture from the run."""
    def check(bundle: str, res: dict):
        path = field_jobs_path(bundle)
        jobs = {r["type"]: r["job"] for r in res["types"] if "job" in r}
        if os.environ.get("FUZER_WRITE_FIXTURES"):
            path.write_text(json.dumps(jobs, indent=1, sort_keys=True, default=_json) + "\n")
        kept = json.loads(path.read_text())
        assert sorted(kept) == sorted(jobs), f"{path.name} is stale: rewrite it with FUZER_WRITE_FIXTURES=1"
        assert {t: _shape(j) for t, j in kept.items()} == {t: _shape(j) for t, j in jobs.items()}, \
            f"the field job changed shape: rewrite {path.name} with FUZER_WRITE_FIXTURES=1 and check the 390 px test"
        return jobs
    return check


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

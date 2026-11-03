"""Equipment performance (#105 B) on the Okan stories: the after-cooler fouling each summer, the 3rd
stage it heats losing efficiency, the bearing wear on 20-K-2200B; peers across the compressor class."""

import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import equipment, mcp_server, plots
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.graph import pipeline, queries
from fuzer.plots import maintenance
from fuzer.sources import ingest

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_cooler_fouling_each_summer_and_clean_after(conn):
    cooler = fid(conn, "20-E-2350A")
    summer = equipment.cooler_fouling(conn, cooler, 180, "2024-08-10T00:00", pack=PACK)
    assert summer["flag"] == "fouling" and summer["value"] - summer["expected"] > 15 and summer["rise_per_day"] > 0
    assert summer["reaches_limit"] == "already past it" and summer["high_limit"] is not None
    assert equipment.cooler_fouling(conn, cooler, 180, "2024-09-20T00:00", pack=PACK)["flag"] is None  # cleaned 19 Aug
    assert equipment.cooler_fouling(conn, cooler, 180, "2025-07-20T00:00", pack=PACK)["flag"] == "fouling"


def test_compressor_efficiency_follows_the_stories(conn):
    stage3 = fid(conn, "20-K-2300A")
    hot = equipment.compressor_performance(conn, stage3, 60, "2024-08-15T00:00")
    assert hot["flag"] == "efficiency_drop" and "surge" in hot["note"] and hot["head_kj_kg"] > 0
    assert equipment.compressor_performance(conn, stage3, 60, "2024-09-25T00:00")["flag"] is None
    worn = equipment.compressor_performance(conn, fid(conn, "20-K-2200B"), 60, "2024-03-10T00:00")
    assert worn["flag"] == "efficiency_drop" and worn["value"] < worn["expected"]
    with pytest.raises(equipment.CalcError, match="first stage"):
        equipment.compressor_performance(conn, fid(conn, "20-K-2100A"), 60, "2024-08-15T00:00")


def test_peers_and_tools(conn, okan_pack):
    p = equipment.peers(conn, fid(conn, "20-K-2200B"))
    assert len(p["units"]) >= 9 and p["flag"] in ("outliers", "class_wide_failures", None)
    assert p["units"][0]["failures_per_year"] >= p["class_median_failures_per_year"]
    out = mcp_server.calc("cooler_fouling", "20-E-2350A", until="2024-08-10T00:00")
    assert out["flag"] == "fouling" and out["check"] == "cooler_fouling"
    for fig in (maintenance.compressor_performance(conn, fid(conn, "20-K-2300A"), 90, "2024-08-15T00:00"),
                maintenance.cooler_fouling(conn, fid(conn, "20-E-2350A"), 240, "2024-08-15T00:00"),
                maintenance.peers(conn, fid(conn, "20-K-2200B"))):
        plots.check(fig)
        assert fig.summary["basis"]

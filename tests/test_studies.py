"""Correlation studies (#106): lag ranking, cleaning and ordering on made-up series; on Okan, what moved
before a well's casing pressure fell when train C tripped; the Parquet cache; the MCP tools."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from fakeplant import okan
from fuzer.agents import mcp_server
from fuzer.analysis import studies
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import ingest

PACK = load_pack("okan")
TRIP = ("2024-11-04T00:00", "2024-11-07T00:00")  # train C trips at 05:30 on the 5th


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    mp.setenv("FUZER_CACHE_DIR", tempfile.mkdtemp(prefix="fzcache"))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


def point(conn, name):
    return conn.execute("select fuzer_id from entity_label join entity e using (fuzer_id) "
                        "where e.kind = 'timeseries' and label = %s", (name,)).fetchone()["fuzer_id"]


def test_lag_sign_cleaning_and_order():
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(size=500))
    y = np.roll(x, 3) + rng.normal(0, 0.05, 500)  # y follows x by 3 steps
    X = np.column_stack([x, -x, rng.normal(size=500)])
    _, lag, r = studies.lagged(X[3:], y[3:], range(-6, 7))
    assert list(lag[:2]) == [3, 3] and r[0] > 0.99 and r[1] < -0.99 and abs(r[2]) < 0.2
    _, lag, _ = studies.lagged(y[3:, None], x[3:], range(-6, 7))
    assert lag[0] == -3  # y moves after x

    M = np.column_stack([x, np.full(500, 2.0), np.where(np.arange(500) % 3 == 0, np.nan, x), np.full(500, np.nan)])
    C, cols, rows = studies.clean(M, keep=np.arange(500) >= 10)
    assert list(cols) == [True, False, True, False] and rows.sum() == 490 and not np.isnan(C).any()

    a, b = rng.normal(size=300), rng.normal(size=300)
    R = np.corrcoef(np.column_stack([a, b, a + 0.1 * rng.normal(size=300), b + 0.1 * rng.normal(size=300)]), rowvar=False)
    o = studies.order(R)
    assert abs(o.index(0) - o.index(2)) == 1 and abs(o.index(1) - o.index(3)) == 1
    assert studies.pearson([1, 2, 3], [1, 2, 3]) is None and studies.pearson([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]) is None


def test_what_moved_before_a_well_lost_its_lift_gas(conn):
    """Rank the whole facility against OK-J01-01's casing pressure across train C's trip: the other gas
    lifted wells move with it, and the points that moved first are the lift gas headers and train C."""
    out = studies.correlate(conn, PACK, point(conn, "OK_J01_01.CASP"), "facility", *TRIP, top=10)
    assert out["candidates"] > 900 and out["ranking"][0]["r"] > 0.98
    first = [x["point"] for x in out["leaders"][:12]]
    assert any("FT-3002" in p or "FT3002" in p for p in first) and any("2000C" in p or "2100C" in p for p in first), first
    assert all(x["leads_by_minutes"] > 0 and x["moves"] == "first" for x in out["leaders"])
    assert any(x["connected"] for x in out["leaders"]) and out["sources"][0] == out["target_id"]
    assert "while the plant ran" in out["basis"]


def test_matrix_is_cached_and_ordered(conn):
    k = mcp_server._resolve(conn, "20-K-2100C")[0]
    pts = studies.points(conn, k)
    a, b = datetime.fromisoformat(TRIP[0]), datetime.fromisoformat(TRIP[1])
    t1, X1 = studies.matrix(pts, a, b, 10)
    files = set(Path(os.environ["FUZER_CACHE_DIR"], "studies").glob("*.parquet"))
    t2, X2 = studies.matrix(pts, a, b, 10)
    assert files and np.array_equal(X1, X2, equal_nan=True) and len(t1) == len(t2) == 433
    m = studies.correlation_matrix(conn, PACK, k, *TRIP, path="flows_to:up")
    assert m["used"] >= 3 and len(m["order"]) == m["used"] == m["matrix"].shape[0]
    assert m["strongest_pairs"][0]["connected"] and abs(m["strongest_pairs"][0]["r"]) > 0.9
    with pytest.raises(studies.CalcError, match="too big"):
        studies.correlation_matrix(conn, PACK, "facility", "2024-01-01T00:00", "2025-01-01T00:00", step=5)


def test_tools_return_the_top_only(conn, okan_pack):
    got = mcp_server.correlate("OK_J01_01.CASP", "facility", *TRIP, top=5)
    assert "ranking" in got, got
    assert len(got["ranking"]) == 5 and got["leaders"]
    m = mcp_server.correlation_matrix("20-K-2100C", *TRIP)
    assert "matrix" not in m and m["strongest_pairs"] and m["order"]
    assert "error" in mcp_server.correlate("OK_J01_01.CASP", "facility", "2024-11-07T00:00", "2024-11-04T00:00")

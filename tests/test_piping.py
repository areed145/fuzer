"""Piping connectivity from P&IDs, and how it compares with the Energy Components network."""

import os
import tempfile

import pytest

from fakeplant import offshore
from fuzer import db, graph, ingest, llm_pids, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_smart_pid_piping(conn):
    net = queries.network(conn, "pipes_to")
    labels = {n["label"] for n in net["nodes"]}
    assert {"20-V-2001", "23-V-2001", "27-V-2001"} <= labels
    down = {r["label"] for r in graph.trace(conn, fid(conn, "20-V-2001"), "pipes_to", "down")}
    assert {"23-V-2001", "27-V-2001"} <= down


def test_compare_finds_the_planted_gap(conn):
    diff = queries.compare_networks(conn)
    gaps = {(r["from_label"], r["to_label"]) for r in diff["in_ec_not_on_pids"]}
    assert ("20-V-2002", "27-V-2001") in gaps      # EC routes it; no P&ID line does
    assert ("20-V-2001", "23-V-2001") not in gaps  # backed by an off-page line on the area 20 P&ID
    assert ("27-V-2001", "27-FT-3001") not in gaps  # the meter has no piping at all, so it can't be compared
    assert isinstance(diff["on_pids_not_in_ec"], list)


def test_claude_connections_from_drawings():
    data = {"title_block": {"drawing_number": "X", "title": "", "revision": ""}, "tags": [],
            "connections": [{"from": "44-P-1001A", "to": "44-P-1001B", "line": '6"-P-44479-A1'},
                            {"from": "44-P-9999", "to": "44-V-2001", "line": "?"}]}
    out = llm_pids.interpret(data, {"sheet": "X", "title": "AREA 44"}, PACK, {"44-P-1001A", "44-P-1001B", "44-V-2001"})
    assert out["connections"] == [{"from": "44-P-1001A", "to": "44-P-1001B", "line": '6"-P-44479-A1'}]
    assert out["findings"][0]["issue"] == "end not in register"


def test_api_network_rel(conn):
    from fastapi.testclient import TestClient
    from fuzer.api import app
    client = TestClient(app)
    assert client.get("/network", params={"rel": "pipes_to"}).json()["edges"]
    assert client.get("/network", params={"rel": "nonsense"}).status_code == 422
    assert set(client.get("/network/compare").json()) == {"in_ec_not_on_pids", "on_pids_not_in_ec"}

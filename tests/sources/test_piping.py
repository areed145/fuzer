"""Piping connectivity from P&IDs, and how it compares with the Energy Components network."""


import pytest

from fuzer.core.pack import load_pack
from fuzer.graph import edges, queries
from fuzer.sources import llm_pids

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_smart_pid_piping(conn):
    net = queries.network(conn, "pipes_to")
    labels = {n["label"] for n in net["nodes"]}
    assert {"10-V-2001", "10-V-2002"} <= labels
    down = {r["label"] for r in edges.trace(conn, fid(conn, "10-V-2001"), "pipes_to", "down")}
    assert down


def test_compare_finds_the_planted_gap(conn):
    diff = queries.compare_networks(conn)
    gaps = {(r["from_label"], r["to_label"]) for r in diff["in_ec_not_on_pids"]}
    assert ("30-V-2002", "10-V-2001") in gaps
    assert isinstance(diff["on_pids_not_in_ec"], list)


def test_claude_connections_from_drawings():
    data = {"title_block": {"drawing_number": "X", "title": "", "revision": ""}, "tags": [],
            "connections": [{"from": "20-K-2200B", "to": "20-K-2300A", "line": '6"-P-44479-A1'},
                            {"from": "20-K-9999", "to": "20-V-2001", "line": "?"}]}
    out = llm_pids.interpret(data, {"sheet": "X", "title": "AREA 20"}, PACK,
                             {"20-K-2200B", "20-K-2300A", "20-V-2001"})
    assert out["connections"] == [{"from": "20-K-2200B", "to": "20-K-2300A", "line": '6"-P-44479-A1'}]
    assert out["findings"][0]["issue"] == "end not in register"


def test_api_network_rel(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    client = TestClient(app)
    assert client.get("/network", params={"rel": "pipes_to"}).json()["edges"]
    assert client.get("/network", params={"rel": "nonsense"}).status_code == 422
    assert set(client.get("/network/compare").json()) == {"in_ec_not_on_pids", "on_pids_not_in_ec"}

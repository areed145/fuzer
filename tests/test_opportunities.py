"""The opportunity scan (#102): the gas lift reallocation the fake data supports, with its gain matching a
calculation by hand from the wells' curves; opportunities on the board ranked against problems; a rejected
one not raised again until the data changes materially."""


import pytest
from fuzer.agents import mcp_server
from fuzer.analysis import calcs
from fuzer.core.pack import load_pack
from fuzer.ops import board, opportunities
from fuzer.workflow import engine

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    engine.load_defaults(c)
    yield c


def test_gas_lift_reallocation_matches_a_hand_calculation(conn):
    moves = opportunities.gas_lift_moves(conn)
    assert moves and all(m["kind"] == "gas_lift_reallocation" for m in moves)
    top = max(moves, key=lambda m: m["gain_sm3d"])
    total = 0.0
    for mv in top["moves"]:  # by hand: each well's curve, its marginal response now, the gas moved
        a = calcs.gas_lift_fit(conn, mv["from_id"], opportunities.BREAKEVEN)
        b = calcs.gas_lift_fit(conn, mv["to_id"], opportunities.BREAKEVEN)
        assert a["g_now"] > a["g_opt"] and b["g_now"] < b["g_opt"]  # past its optimum, short of its own
        total += mv["ksm3d"] * (b["marginal"] - a["marginal"])
    assert abs(total - top["gain_sm3d"]) <= 0.5 and top["evidence"] and top["confirm_by"]
    given = sum(mv["ksm3d"] for mv in top["moves"])
    assert given == pytest.approx(top["lift_gas_moved_ksm3d"], abs=0.2)  # gas moved within the header, not added


def test_scan_board_and_rejection(conn):
    first = opportunities.scan(conn, PACK)
    assert first["new"] == first["found"] > 0
    out = board.refresh(conn, PACK)
    page = board.cards(conn, per_page=500)["cards"]
    opp = [c for c in page if c["kind"] in ("opportunity",) or any(r.startswith("opportunity:scan:") for r in c["records"])]
    assert opp and {"work_order", "maintenance_plan"} <= set(out["by_kind"])  # ranked against problems
    held = next(c for c in opp if any(r.startswith("work_item:") for r in c["records"]))  # scan item and work item: one card
    assert any(r.startswith("opportunity:scan:") for r in held["records"])

    key = max(opportunities.listing(conn), key=lambda o: o["gain_sm3d"] or 0)["key"]
    with pytest.raises(ValueError, match="reason"):
        opportunities.decide(conn, key, "rejected", "alice")
    opportunities.decide(conn, key, "rejected", "alice", "lift gas compressor is being re-wheeled; revisit in Q2")
    again = opportunities.scan(conn, PACK)
    assert again["still_decided"] >= 1 and again["reopened"] == 0
    assert key not in [o["key"] for o in opportunities.listing(conn)]

    # the data changes materially (half the gain): it comes back
    conn.execute("update opportunity set gain_sm3d = gain_sm3d * 3 where key = %s", (key,))
    assert opportunities.scan(conn, PACK)["reopened"] == 1
    assert key in [o["key"] for o in opportunities.listing(conn)]
    got = mcp_server.opportunities()
    assert got["count"] >= 1 and got["opportunities"][0]["detail"]["basis"]

    proj = opportunities.decide(conn, key, "project", "bob")
    assert proj["project"] and board.refresh(conn, PACK)["by_kind"].get("project")

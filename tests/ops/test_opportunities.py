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
    top = max(moves, key=lambda m: m["gain_bbld"])
    total = 0.0
    for mv in top["moves"]:  # by hand: each well's curve, its marginal response now, the gas moved
        a = calcs.gas_lift_fit(conn, mv["from_id"], opportunities.BREAKEVEN)
        b = calcs.gas_lift_fit(conn, mv["to_id"], opportunities.BREAKEVEN)
        assert a["g_now"] > a["g_opt"] and b["g_now"] < b["g_opt"]  # past its optimum, short of its own
        total += mv["mscfd"] * (b["marginal"] - a["marginal"])
    assert abs(total - top["gain_bbld"]) <= 0.5 and top["evidence"] and top["confirm_by"]
    given = sum(mv["mscfd"] for mv in top["moves"])
    assert given == pytest.approx(top["lift_gas_moved_mscfd"], abs=0.2)  # gas moved within the header, not added


def test_scan_board_and_rejection(conn):
    first = opportunities.scan(conn, PACK)
    assert first["new"] == first["found"] > 0
    out = board.refresh(conn, PACK)
    page = board.cards(conn, per_page=500)["cards"]
    opp = [c for c in page if c["kind"] in ("opportunity",) or any(r.startswith("opportunity:scan:") for r in c["records"])]
    assert opp and {"work_order", "maintenance_plan"} <= set(out["by_kind"])  # ranked against problems
    held = next(c for c in opp if any(r.startswith("work_item:") for r in c["records"]))  # scan item and work item: one card
    item_id = conn.execute("select item_id from opportunity where status = 'open' limit 1").fetchone()["item_id"]
    assert conn.execute("select process_key from work_process where item_id = %s",  # the engineer's SLA (#509)
                        (item_id,)).fetchone()["process_key"] == "task"
    assert any(r.startswith("opportunity:scan:") for r in held["records"])

    key = max(opportunities.listing(conn), key=lambda o: o["gain_bbld"] or 0)["key"]
    with pytest.raises(ValueError, match="reason"):
        opportunities.decide(conn, key, "rejected", "alice")
    opportunities.decide(conn, key, "rejected", "alice", "lift gas compressor is being re-wheeled; revisit in Q2")
    again = opportunities.scan(conn, PACK)
    assert again["still_decided"] >= 1 and again["reopened"] == 0
    assert key not in [o["key"] for o in opportunities.listing(conn)]

    # the data changes materially (half the gain): it comes back
    conn.execute("update opportunity set gain_bbld = gain_bbld * 3 where key = %s", (key,))
    assert opportunities.scan(conn, PACK)["reopened"] == 1
    assert key in [o["key"] for o in opportunities.listing(conn)]
    got = mcp_server.opportunities()
    assert got["count"] >= 1 and got["opportunities"][0]["detail"]["basis"]

    proj = opportunities.decide(conn, key, "project", "bob")
    assert proj["project"] and board.refresh(conn, PACK)["by_kind"].get("project")


def test_two_board_refreshes_at_once_take_turns(conn, okan_db):
    """The 5-minute detect refreshed the board while another refresh was mid-way: both emptied board_card, then both
    inserted the same cards, and one failed on its key (#1152). The second now waits for the first to commit."""
    import threading

    from fuzer.core import db

    done = []
    conn.commit()
    with db.connect(url=okan_db.url) as first:
        first.execute("select pg_advisory_xact_lock(hashtext('board_refresh'))")  # a refresh mid-way
        first.execute("delete from board_card")

        def second():
            with db.connect(url=okan_db.url) as c:
                done.append(board.refresh(c, PACK))

        t = threading.Thread(target=second)
        t.start()
        t.join(1.5)
        assert t.is_alive() and not done  # waiting its turn
        board.refresh(first, PACK)
        t.join(60)
    assert done and done[0]["cards"] > 0 and done[0]["new"] == 0


def test_a_database_from_before_us_units_keeps_its_decided_opportunities():
    """The gain column was gain_sm3d before #1159; without its move every scan on such a database failed on
    'gain_bbld', and with it the deploy's data refresh and the 5-minute detectors."""
    from fuzer.core import db as core_db

    with core_db.connect() as conn:
        core_db.reset(conn)
        core_db.init_schema(conn)
        conn.execute("alter table opportunity rename column gain_bbld to gain_sm3d")
        conn.execute("insert into opportunity (key, kind, title, gain_sm3d, detail, fingerprint, status) "
                     "values ('k', 'gas_lift', 't', 10, '{}', 'f', 'rejected')")
        conn.execute("update schema_version set hash = 'before'")
        conn.commit()
        core_db.init_schema(conn)
        row = conn.execute("select * from opportunity").fetchone()
        assert row["gain_bbld"] == 62.9 and "gain_sm3d" not in row and row["status"] == "rejected"

"""Flow links that disagree with the drawings: where the flow network (Energy Components' flows_to) and the drawn
piping (pipes_to) disagree inside one area, a question in the Review queue, never a change to either side."""

from fastapi.testclient import TestClient

from fuzer.core.pack import load_pack
from fuzer.graph.queries.network import flow_disagreements

PACK = load_pack("okan")


def _area(label):
    return next((m.area for m in PACK.tags.find_all(label)), None)


def _flow_questions(conn) -> dict[str, dict]:
    return {r["payload"]["tag"]: {"id": r["id"], **r["payload"]} for r in conn.execute(
        "select id, payload from review_item where kind = 'pid_mismatch' and status <> 'stale' "
        "and payload->>'check' = 'flow'")}  # open, or decided by the answer test (any order)


def test_the_lp_separator_flow_link_skips_what_the_drawings_show(okan_db):
    q = _flow_questions(okan_db.conn)["10-V-2002>10-FT-3001"]
    # Energy Components sends the LP separator straight to the fiscal meter; the drawings run it through the test
    # separator, cooler, surge drum and export pumps, and never draw the meter on a line
    assert q["direction"] == "flow_only" and q["systems"] == ["ec"] and q["sheet"] == "OKAN-10-PID-001"
    assert q["drawn_path"][:4] == [["10-V-2003"], ["10-E-3001"], ["10-V-2004"], ["10-P-1001A"]]
    assert "10-V-2002 → 10-V-2003 → 10-E-3001" in q["question"] and "isn't on any drawn line" in q["question"]
    assert q["fuzer_id"] and q["to_id"]


def test_links_between_areas_are_pipelines_not_drawing_questions(okan_db):
    links = {(d["from"], d["to"]) for d in flow_disagreements(okan_db.conn, _area)}
    assert ("30-V-2002", "10-V-2001") not in links and ("10-V-2001", "20-V-2001") not in links
    assert ("10-V-2001", "10-V-2002") not in links  # drawn and allocated alike


def test_a_drawn_line_the_flow_network_lacks_is_asked_too(okan_db):
    conn = okan_db.conn
    try:
        conn.execute("delete from edge where rel = 'flows_to' and src = (select fuzer_id from entity_label where "
                     "label = '10-V-2001') and dst = (select fuzer_id from entity_label where label = '10-V-2002')")
        found = {(d["from"], d["to"]): d for d in flow_disagreements(conn, _area)}
        d = found[("10-V-2001", "10-V-2002")]
        assert d["kind"] == "drawn_only" and d["drawn_path"] == [["10-V-2002"]]
        assert d["sheets"] == ["OKAN-10-PID-001"]
    finally:
        conn.rollback()


def test_an_answer_drafts_the_fix_for_the_side_that_is_wrong(okan_db):
    from fuzer import api

    conn = okan_db.conn
    qs = _flow_questions(conn)
    lp, train = qs["10-V-2002>10-FT-3001"], next(v for k, v in qs.items() if k.startswith("20-K-2300"))
    with TestClient(api.app) as client:
        assert client.post(f"/review/{lp['id']}", json={"decision": "choose", "value": "system"}).status_code == 200
        (redline,) = client.get(f"/review/{lp['id']}/corrections").json()
        assert redline["kind"] == "drawing_correction" and redline["system"] == "pid"
        assert client.post(f"/review/{train['id']}", json={"decision": "choose", "value": "drawing"}).status_code == 200
        (change,) = client.get(f"/review/{train['id']}/corrections").json()
        assert change["kind"] == "master_data" and change["system"] == "ec"
    # neither answer touched the register's links
    assert conn.execute("select count(*) as n from edge e join entity_label l on l.fuzer_id = e.src "
                        "where e.rel = 'flows_to' and l.label = '10-V-2002'").fetchone()["n"] == 1
    conn.commit()

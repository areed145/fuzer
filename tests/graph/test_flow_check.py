"""Flow links that disagree with the drawings: where the flow network (Energy Components' flows_to) and the drawn
piping (pipes_to) disagree inside one area, a question in the Review queue, never a change to either side.

Okan's drawings and line list agree with its flow network, so each test makes the disagreement it needs and puts
the plant back."""

from fastapi.testclient import TestClient

from fuzer.core.pack import load_pack
from fuzer.graph.queries.network import flow_disagreements
from fuzer.graph.resolve import replace_open_reviews
from fuzer.sources import llm_pids

PACK = load_pack("okan")


def _area(label):
    return next((m.area for m in PACK.tags.find_all(label)), None)


def _without_lines_into(conn, label: str) -> None:
    conn.execute("delete from edge where rel = 'pipes_to' and dst = (select fuzer_id from entity_label "
                 "where label = %s)", (label,))


def test_the_drawings_reach_every_export_meter_the_flow_network_names(okan_db):
    # the export pumps run side by side from the surge drum, and the line list carries each line into the meter
    found = flow_disagreements(okan_db.conn, _area)
    assert not found, [(d["from"], d["to"]) for d in found]


def test_a_flow_link_no_drawn_line_follows_is_asked(okan_db):
    conn = okan_db.conn
    try:
        _without_lines_into(conn, "10-FT-3001")
        d = next(d for d in flow_disagreements(conn, _area) if (d["from"], d["to"]) == ("10-V-2002", "10-FT-3001"))
        # Energy Components sends the LP separator to the fiscal meter; the drawings run it through the test
        # separator, cooler, surge drum and the three export pumps side by side, and stop there
        assert d["kind"] == "flow_only" and d["systems"] == ["ec"] and not d["on_drawings"]
        assert d["drawn_path"][:4] == [["10-V-2003"], ["10-E-3001"], ["10-V-2004"],
                                       ["10-P-1001A", "10-P-1001B", "10-P-1001C"]]
        [(_, q)] = llm_pids.mismatches(conn, {}, [], PACK, [d])
        assert q["check"] == "flow" and q["sheet"] == "OKAN-10-PID-001" and q["tag"] == "10-V-2002>10-FT-3001"
        assert "10-V-2002 → 10-V-2003 → 10-E-3001" in q["question"] and "isn't on any drawn line" in q["question"]
    finally:
        conn.rollback()


def test_links_between_areas_are_pipelines_not_drawing_questions(okan_db):
    conn = okan_db.conn
    try:
        _without_lines_into(conn, "10-V-2001")
        links = {(d["from"], d["to"]) for d in flow_disagreements(conn, _area)}
        assert ("30-V-2002", "10-V-2001") not in links and ("10-V-2001", "20-V-2001") not in links
    finally:
        conn.rollback()


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
    for label in ("10-FT-3001", "50-FT-3001"):
        _without_lines_into(conn, label)
    flows = [d for d in flow_disagreements(conn, _area) if d["to"] in ("10-FT-3001", "50-FT-3001")]
    conn.rollback()  # the lines stay in the register; only the questions are kept
    replace_open_reviews(conn, "pid_mismatch", llm_pids.mismatches(conn, {}, [], PACK, flows))
    conn.commit()
    qs = {r["payload"]["tag"]: r["id"] for r in conn.execute(
        "select id, payload from review_item where kind = 'pid_mismatch' and status = 'open' "
        "and payload->>'check' = 'flow'")}
    with TestClient(api.app) as client:
        lp = qs["10-V-2002>10-FT-3001"]
        assert client.post(f"/review/{lp}", json={"decision": "choose", "value": "system"}).status_code == 200
        (redline,) = client.get(f"/review/{lp}/corrections").json()
        assert redline["kind"] == "drawing_correction" and redline["system"] == "pid"
        nwp = qs["50-V-2001>50-FT-3001"]
        assert client.post(f"/review/{nwp}", json={"decision": "choose", "value": "drawing"}).status_code == 200
        (change,) = client.get(f"/review/{nwp}/corrections").json()
        assert change["kind"] == "master_data" and change["system"] == "ec"
    # neither answer touched the register's links
    assert conn.execute("select count(*) as n from edge e join entity_label l on l.fuzer_id = e.src "
                        "where e.rel = 'flows_to' and l.label = '10-V-2002'").fetchone()["n"] == 1
    conn.commit()

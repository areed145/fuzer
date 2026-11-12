"""P2.4: an answer that says a source is wrong drafts the correction for that source's owner; the question stays
answered while it is out and comes back to the steward with what happened."""

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.core import work
from fuzer.graph import data_work
from fuzer.ops import corrections, writeback

pytestmark = pytest.mark.sequence


def _answer(item_id: int, decision: str, value=None) -> dict:
    from fuzer import api

    with TestClient(api.app) as client:
        r = client.post(f"/review/{item_id}", json={"decision": decision, "value": value, "by": "ana"})
        assert r.status_code == 200, r.text
        return client.get(f"/review/{item_id}/corrections").json()


def _conflict(conn) -> dict:
    """An open conflict whose systems genuinely disagree (not just in how they write it); Okan plants one, and more
    are made like it on other equipment."""
    for r in conn.execute("select id, subject, payload from review_item where kind = 'conflict' and status = 'open' "
                          "order by id"):
        if len({"".join(c for c in str(v["value"]).lower() if c.isalnum()) for v in r["payload"]["values"]}) > 1:
            return r
    fid = conn.execute("select e.fuzer_id from entity e where e.kind = 'equipment' and e.merged_into is null and not "
                       "exists (select 1 from review_item r where r.subject = e.fuzer_id || ':manufacturer') "
                       "order by 1 limit 1").fetchone()["fuzer_id"]
    payload = {"fuzer_id": fid, "field": "manufacturer", "chosen": "Flowserve",
               "values": [{"source": "sap", "source_key": "1", "value": "Flowserve"},
                          {"source": "jde", "source_key": "2", "value": "Sulzer"}]}
    return conn.execute("insert into review_item (kind, subject, payload) values ('conflict', %s, %s) "
                        "returning id, subject, payload", (f"{fid}:manufacturer", Jsonb(payload))).fetchone()


def test_choosing_a_systems_value_drafts_a_change_for_each_system_that_is_wrong(okan_db):
    conn = okan_db.conn
    item = _conflict(conn)
    p = item["payload"]
    right = p["values"][0]
    out = _answer(item["id"], "choose", right["value"])
    wrong = {v["source"] for v in p["values"] if str(v["value"]).lower() != str(right["value"]).lower()}
    assert {c["system"] for c in out} == wrong and all(c["kind"] == "master_data" for c in out)
    for c in out:
        wb = writeback.get(conn, c["writeback_id"])
        assert wb["state"] == "awaiting_approval" and wb["kind"] == "master_data"
        assert wb["payload"]["to"] == right["value"] and wb["source_ref"] == f"review:{item['id']}"
        assert conn.execute("select state from work_item where id = %s", (wb["item_id"],)).fetchone()["state"] == "open"
    assert corrections.sync(conn) == {"drafted": 0, "done": 0, "returned": 0}  # drafted once

    # The next rebuild records what each source says where the steward chose; the question is not asked again.
    from fuzer.core.pack import load_pack
    from fuzer.ops import pipeline

    pipeline.rebuild(conn, load_pack("okan"))
    c = out[0]
    says = conn.execute("select value from reviewed_value where fuzer_id = %s and field = %s and source = %s",
                        (p["fuzer_id"], p["field"], c["system"])).fetchone()
    assert says and str(says["value"]).lower() != str(right["value"]).lower()  # it still says what it said
    assert conn.execute("select status from review_item where id = %s", (item["id"],)).fetchone()["status"] == "decided"
    # The source takes it: what it says on its next load is the steward's value.
    conn.execute("update reviewed_value set value = %s where fuzer_id = %s and field = %s and source = %s",
                 (Jsonb(right["value"]), p["fuzer_id"], p["field"], c["system"]))
    assert corrections.sync(conn)["done"] >= 1
    row = conn.execute("select state, outcome from source_correction where id = %s", (c["id"],)).fetchone()
    assert row["state"] == "done" and "now says" in row["outcome"]
    assert conn.execute("select status from review_item where id = %s", (item["id"],)).fetchone()["status"] == "decided"
    conn.commit()


def test_an_owner_who_rejects_the_change_sends_the_question_back_to_the_steward(okan_db):
    conn = okan_db.conn
    item = _conflict(conn)
    conn.commit()
    out = _answer(item["id"], "accept")
    assert out, "keeping the chosen value says the others are wrong"
    writeback.reject(conn, out[0]["writeback_id"], "bo", reason="the nameplate agrees with us")
    assert corrections.sync(conn)["returned"] == 1
    row = conn.execute("select status, payload from review_item where id = %s", (item["id"],)).fetchone()
    assert row["status"] == "open" and "the nameplate agrees with us" in row["payload"]["returned"]["why"]
    data_work.sync(conn)  # and it is a case in the steward's queue again
    key = f"conflict:{conn.execute('select subject from review_item where id = %s', (item['id'],)).fetchone()['subject']}"
    assert conn.execute("select state from exception_record where type = 'conflicting_sources' and key = %s",
                        (key,)).fetchone()["state"] == "open"
    conn.commit()


def test_check_it_in_the_field_opens_a_field_check_that_comes_back_to_the_steward(okan_db):
    conn = okan_db.conn
    item = _conflict(conn)
    conn.commit()
    (c,) = [x for x in _answer(item["id"], "field") if x["state"] == "open"]  # a reopened question keeps its history
    assert c["kind"] == "field_check" and c["item_id"]
    case = conn.execute("select state, title, role, assignee from work_item where id = %s", (c["item_id"],)).fetchone()
    assert case["state"] == "open" and case["title"].startswith("Check ")
    work.comment(conn, c["item_id"], "olu", "Nameplate reads Sulzer")
    work.close_case(conn, c["item_id"], "done", "olu", reason="checked")
    assert corrections.sync(conn)["returned"] == 1
    row = conn.execute("select status, payload from review_item where id = %s", (item["id"],)).fetchone()
    assert row["status"] == "open" and row["payload"]["returned"]["why"].startswith("Checked in the field")
    conn.commit()


def test_a_drawing_disagreement_drafts_a_redline_or_a_register_change(okan_db):
    conn = okan_db.conn
    items = conn.execute("select id, payload from review_item where kind = 'pid_mismatch' and status = 'open' "
                         "order by id").fetchall()
    assert items, "the Okan drawings disagree with the systems somewhere"
    (redline,) = _answer(items[0]["id"], "choose", "system")  # the drawing needs changing
    assert redline["kind"] == "drawing_correction" and redline["system"] == "pid"
    assert writeback.get(conn, redline["writeback_id"])["kind"] == "drawing_correction"
    placed = next((i for i in items[1:] if i["payload"].get("check") == "placement"), None)
    if placed:
        out = _answer(placed["id"], "choose", "drawing")  # the register needs changing
        assert out and {c["kind"] for c in out} == {"master_data"}
    conn.commit()


def test_an_answered_question_stays_answered_through_a_rebuild(okan_db):
    from fuzer.core.pack import load_pack
    from fuzer.ops import pipeline

    conn = okan_db.conn
    decided = {r["id"] for r in conn.execute("select review_item_id as id from source_correction where state = 'open'")}
    pipeline.rebuild(conn, load_pack("okan"))
    assert decided and all(conn.execute("select status from review_item where id = %s", (i,)).fetchone()["status"]
                           in ("decided", "stale") for i in decided)
    conn.commit()


def test_kestrel_corrections_go_to_its_own_systems(request):
    conn = request.getfixturevalue("kestrel")
    kinds = {r["kind"]: r["n"] for r in conn.execute(
        "select kind, count(*) as n from review_item where status = 'open' group by kind")}
    item = _conflict(conn)
    conn.commit()
    out = _answer(item["id"], "choose", item["payload"]["values"][0]["value"])
    assert out and all(c["kind"] == "master_data" and c["writeback_id"] for c in out)
    if kinds.get("pid_mismatch"):
        drawing = conn.execute("select id from review_item where kind = 'pid_mismatch' and status = 'open' "
                               "order by id limit 1").fetchone()
        (redline,) = _answer(drawing["id"], "choose", "system")
        assert redline["kind"] == "drawing_correction"

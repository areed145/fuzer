"""Where a value came from (#875): on Okan's train B 2nd stage compressor, the systems whose records make it up and how
each was matched, which values each supplies and by what rule, a reviewer's choice, a steward's fix, a merge, and the
work and write-back about it, each read from what the register keeps."""

from psycopg.types.json import Jsonb

from fuzer.core.exceptions import raise_exception
from fuzer.graph import lineage
from fuzer.ops import rounds  # noqa: F401  (declares round_finding)


def test_the_compressor_says_where_its_values_came_from(okan_db):
    conn = okan_db.conn
    fid = conn.execute("select fuzer_id from entity_label where label = '20-K-2200B'").fetchone()["fuzer_id"]
    got = lineage.lineage(conn, fid)
    by = {s["source"]: s for s in got["systems"]}
    assert by["sap"]["records"][0]["record"] == "sap:floc:OKAN-20-K2200B"
    assert by["sap"]["records"][0]["matched_by"] == "same key" and "criticality" in by["sap"]["chosen_fields"]
    assert "pi_af" in by and sum(got["rules"].values()) == conn.execute(
        "select count(*) as n from golden where fuzer_id = %s", (fid,)).fetchone()["n"]
    assert got["reviewed"] == [] and got["identity"] == [] and got["work"]["count"] == 0

    try:
        conn.execute("update golden set source = 'review', rule = 'review' where fuzer_id = %s and field = 'description'",
                     (fid,))
        conn.execute("insert into review_item (kind, subject, payload, status, decided_by, decided_at) values "
                     "('conflict', %s, '{}', 'decided', 'steward-sam', now())", (f"{fid}:description",))
        conn.execute("insert into steward_fix (batch, fuzer_id, field, old_value, new_value, created_by, reason) values "
                     "('b1', %s, 'criticality', %s, %s, 'steward-sam', 'the FMECA says A')", (fid, Jsonb("B"), Jsonb("A")))
        conn.execute("insert into id_event (event, fuzer_id, other_id) values ('merge', 'fz-equipment-old', %s)", (fid,))
        x = raise_exception(conn, "round_finding", "lineage-test", "K-2200B seal weeping", "olga",
                            refs=[fid])
        conn.execute("insert into writeback (system, kind, record, idem_key, drafted_by, item_id, state, approved_by) "
                     "values ('sap_pm', 'notification', '10004711', 'lineage-test', 'test', %s, 'approved', 'planner-pat')",
                     (x["item_id"],))
        got = lineage.lineage(conn, fid)
        assert got["reviewed"] == [{"field": "description", "value": got["reviewed"][0]["value"], "by": "steward-sam",
                                    "at": got["reviewed"][0]["at"]}]
        assert got["fixes"][0]["reason"] == "the FMECA says A" and got["fixes"][0]["new_value"] == "A"
        assert got["identity"][0]["event"] == "merge" and got["identity"][0]["other"] == "fz-equipment-old"
        assert got["work"]["count"] == 1 and got["work"]["recent"][0]["id"] == x["item_id"]
        assert got["writebacks"][0]["approved_by"] == "planner-pat" and got["writebacks"][0]["system"] == "sap_pm"
        assert lineage.lineage(conn, "fz-no-such") is None
    finally:
        conn.rollback()

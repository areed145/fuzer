"""P2.3c: one answer resolves a group of like questions, each answer pinned where the next rebuild reads it, and a
standing answer decides new ones like them as they arrive."""

import pytest
from psycopg.types.json import Jsonb

from fuzer.core.pack import load_pack
from fuzer.graph import review
from fuzer.ops import pipeline

pytestmark = pytest.mark.sequence


def _open(conn, kind: str) -> list[dict]:
    return conn.execute("select id, subject, payload from review_item where status = 'open' and kind = %s",
                        (kind,)).fetchall()


def test_like_conflicts_are_one_group_answered_by_naming_the_right_system(okan_db):
    conn = okan_db.conn
    first = _open(conn, "conflict")[0]
    p = first["payload"]
    other = conn.execute("select fuzer_id from entity where kind = 'equipment' and fuzer_id <> %s and merged_into is null "
                         "order by fuzer_id limit 1", (p["fuzer_id"],)).fetchone()["fuzer_id"]
    # A second item whose systems write the same maker differently: like the first, so one answer covers both.
    twin = {**p, "fuzer_id": other, "values": [{**v, "value": str(v["value"]).swapcase()} for v in p["values"]]}
    conn.execute("insert into review_item (kind, subject, payload) values ('conflict', %s, %s)",
                 (f"{other}:{p['field']}", Jsonb(twin)))
    g = next(g for g in review.groups(conn, "conflict") if first["id"] in g["items"])
    assert g["count"] == 2 and g["label"].startswith(f"{p['field']}: ")
    sources = sorted({v["source"] for v in p["values"]})
    assert [o["source"] for o in g["options"] if o.get("source")] == sources

    out = review.decide_group(conn, g["key"], "choose", "ana", source="sap")
    assert sorted(out["decided"]) == sorted(g["items"]) and not out["skipped"]
    for fid, item in ((p["fuzer_id"], p), (other, twin)):
        want = next(v["value"] for v in item["values"] if v["source"] == "sap")
        row = conn.execute("select value from assertion where fuzer_id = %s and field = %s and source = 'review'",
                           (fid, p["field"])).fetchone()
        assert row["value"] == want
    decided = conn.execute("select decision from review_item where id = %s", (first["id"],)).fetchone()["decision"]
    assert decided["group"] == g["key"] and decided["source"] == "sap"
    # Their cases closed in the steward's queue.
    assert conn.execute("select count(*) as n from exception_record where type = 'conflicting_sources' "
                        "and state = 'open'").fetchone()["n"] == 0
    conn.commit()


def test_a_standing_answer_keeps_like_matches_apart_through_rebuilds_and_decides_new_ones(okan_db):
    conn = okan_db.conn
    g = review.groups(conn, "match")[0]
    assert g["count"] >= 2
    out = review.decide_group(conn, g["key"], "reject", "ana", standing=True)
    assert sorted(out["decided"]) == sorted(g["items"]) and out["rebuild"]
    pinned = conn.execute("select count(*) as n from pinned_link where rule = 'cannot' and decided_by = 'ana'").fetchone()
    assert pinned["n"] >= g["count"]
    conn.commit()

    pipeline.rebuild(conn, load_pack("okan"))
    # Pinned: none of them comes back, and the group has nothing open.
    assert conn.execute("select count(*) as n from review_item where id = any(%s) and status = 'open'",
                        (g["items"],)).fetchone()["n"] == 0
    assert not any(x["key"] == g["key"] for x in review.groups(conn, "match", least=1))

    # A new question like them is decided by the standing answer, as the person who set it.
    sample = conn.execute("select payload from review_item where id = %s", (g["items"][0],)).fetchone()["payload"]
    new = {**sample, "left": {**sample["left"], "ref": sample["left"]["ref"] + "-NEW"}}
    nid = conn.execute("insert into review_item (kind, subject, payload) values ('match', 'new-like-pair', %s) "
                       "returning id", (Jsonb(new),)).fetchone()["id"]
    assert review.apply_standing(conn) == {g["key"]: 1}
    row = conn.execute("select status, decided_by, decision from review_item where id = %s", (nid,)).fetchone()
    assert row["status"] == "decided" and row["decided_by"] == "ana" and row["decision"]["decision"] == "reject"

    # Withdrawn: the next one waits for a person; what it decided stays decided.
    assert review.withdraw(conn, g["key"], "ana")
    conn.execute("insert into review_item (kind, subject, payload) values ('match', 'another-like-pair', %s)",
                 (Jsonb({**new, "left": {**new["left"], "ref": new["left"]["ref"] + "2"}}),))
    assert review.apply_standing(conn) == {}
    assert conn.execute("select status from review_item where id = %s", (nid,)).fetchone()["status"] == "decided"
    conn.rollback()


def test_the_api_lists_groups_and_answers_one(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api

    with TestClient(api.app) as client:
        groups = client.get("/review/groups").json()
        assert all(g["count"] >= 2 and g["options"] for g in groups)
        assert client.post("/review/groups", json={"key": "match|nobody+nothing|x", "decision": "accept",
                                                   "by": "ana"}).status_code == 404
        assert client.post("/review/groups", json={"key": "conflict|model|maximo+sap|values", "decision": "choose",
                                                   "by": "ana"}).status_code == 400  # name the right system

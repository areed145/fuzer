"""P2.3: every open question about the register is a case in the data steward's queue, in the same work engine as
the IOC's, and answering it closes the case."""

import pytest

from fuzer.graph import data_work
from fuzer.ops import routing

pytestmark = pytest.mark.sequence


def _open(conn) -> dict[str, dict]:
    return {r["key"]: r for r in conn.execute(
        "select x.*, w.state as case_state, w.assignee, w.role from exception_record x "
        "left join work_item w on w.id = x.item_id where x.type = any(%s) and x.state = 'open'",
        ([t.name for t in data_work.TYPES.values()],))}


def test_every_open_question_is_a_case_in_the_stewards_queue(okan_db):
    conn = okan_db.conn
    questions = {f"{r['kind']}:{r['subject']}": r for r in conn.execute(
        "select kind, subject from review_item where status = 'open'")}
    assert questions, "the Okan build leaves questions for the steward"
    work = _open(conn)
    assert set(work) == set(questions)
    for x in work.values():
        assert x["item_id"] and x["case_state"] == "open" and "data_steward" in (x["role"] or x["assignee"] or ""), x
        assert "fz-" not in x["title"], x["title"]  # a steward reads words, not ids
    # The match questions name both records and the tag they share.
    match = next(x for k, x in work.items() if k.startswith("match:"))
    assert match["title"].startswith("Same item? ") and " at " in match["title"]
    # Its evidence links to the question, opened on it on Master data, and says what was scored.
    item = conn.execute("select id from review_item where kind = 'match' and subject = %s",
                        (match["key"].removeprefix("match:"),)).fetchone()["id"]
    items = {i["label"]: i for i in match["evidence"]["finding"]["items"]}
    assert items["Answer"] == {"label": "Answer", "kind": "page", "text": "Answer it on Master data",
                               "value": f"#/masterdata?tab=questions&kind=match&item={item}"}
    assert items["Match score"]["kind"] == "number"
    conn.rollback()


def test_the_types_are_on_routing_with_the_stewards_role(okan_db):
    conn = okan_db.conn
    routes = {r["type"]: r for r in routing.routes(conn)}
    for name, label, _ in data_work.KINDS.values():
        assert routes[name]["label"] == label
    conn.rollback()


def test_answering_closes_the_case_and_a_rebuild_does_not_bring_it_back(okan_db):
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.core.pack import load_pack
    from fuzer.ops import pipeline

    conn = okan_db.conn
    item = conn.execute("select id, kind, subject from review_item where status = 'open' and kind = 'conflict' "
                        "order by id limit 1").fetchone()
    key = f"conflict:{item['subject']}"
    case = _open(conn)[key]["item_id"]
    conn.rollback()
    with TestClient(api.app) as client:
        assert client.post(f"/review/{item['id']}", json={"decision": "accept", "by": "ana"}).status_code == 200
    x = conn.execute("select state from exception_record where type = 'conflicting_sources' and key = %s", (key,)).fetchone()
    assert x["state"] != "open"
    assert conn.execute("select state from work_item where id = %s", (case,)).fetchone()["state"] != "open"
    pipeline.rebuild(conn, load_pack("okan"))
    assert key not in _open(conn)
    assert sum(1 for k in _open(conn) if k == key) == 0
    conn.commit()


def test_a_drawing_line_question_is_about_the_two_items_it_would_join():
    """A line only one drawing shows names its two ends, so the steward's case shows on both (#747)."""
    p = {"sheet": "S-1", "from": "10-V-2004", "to": "10-P-1001A", "line": "16\"-P-10950-A1A"}
    assert data_work.refs("pid_connection", p) == ["tag:10-V-2004", "tag:10-P-1001A"]
    assert data_work.refs("pid_connection", {**p, "from": None}) == ["tag:10-P-1001A"]  # the other end unread

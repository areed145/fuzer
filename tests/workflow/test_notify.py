"""Telling the assignee (P5.2, #604): work sent, handed back, escalated or coming due tells the person concerned,
with a link to the case, and nobody away is told."""

import os
import tempfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fuzer.core import db
from fuzer.workflow import engine, notify, roles


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        for name, r in (("ines", ["ops_lead"]), ("olav", ["operator"]), ("pia", ["operator"]), ("rune", ["operator"])):
            engine.set_person(c, name, r)
        c.commit()
        notify.scan(c)  # the first scan starts from now
        yield c


@pytest.fixture(scope="module")
def client(conn):
    from fuzer.api import app

    return TestClient(app)


def told(conn, person: str) -> list[dict]:
    notify.scan(conn)
    return notify.mine(conn, person)["notifications"]


def case(conn, title: str, **kw) -> int:
    out = engine.create_item(conn, "task", title, "ines", **kw)
    conn.commit()
    return out["id"]


def test_history_tells_nobody(conn):
    assert all(n["kind"] != "assigned" or "Before" not in n["text"] for n in told(conn, "olav"))


def test_a_dispatched_operator_is_told_with_a_link_and_can_mark_it_read(conn, client):
    item = case(conn, "Check the pump seal")
    engine.dispatch_item(conn, item, "ines", "olav", "seal weeping at the coupling")
    conn.commit()
    body = client.get("/work/notifications", params={"person": "olav"}).json()
    n = body["notifications"][0]
    assert n["kind"] == "dispatched" and n["link"] == f"#/cases/{item}"
    assert n["text"] == "ines sent you 'Check the pump seal'. seal weeping at the coupling"
    assert body["unread"] >= 1
    assert client.post("/work/notifications/read", json={"person": "olav", "ids": [n["id"]]}).json() == {"read": 1}
    assert client.get("/work/notifications", params={"person": "olav"}).json()["notifications"][0]["read_at"]


def test_handing_back_tells_whoever_sent_it(conn):
    item = case(conn, "Read the flare meter", assignee="ines")
    engine.dispatch_item(conn, item, "ines", "pia")
    conn.commit()
    engine.hand_back_item(conn, item, "pia", "pia", "meter reads zero")
    conn.commit()
    n = told(conn, "ines")[0]
    assert n["kind"] == "handed_back" and n["item_id"] == item and "pia handed back" in n["text"]


def test_moving_work_yourself_tells_nobody(conn):
    item = case(conn, "Log the tank level")
    before = len(told(conn, "ines"))
    engine.dispatch_item(conn, item, "ines", "ines")
    conn.commit()
    assert len(told(conn, "ines")) == before


def test_nothing_is_sent_to_someone_away_and_their_delegate_is_told(conn):
    now = engine.utcnow()
    roles.set_away(conn, "rune", "ines", now + timedelta(days=1))
    conn.commit()
    item = case(conn, "Open the drain valve")
    engine.dispatch_item(conn, item, "ines", "rune")
    conn.commit()
    assert not [n for n in told(conn, "rune") if n["item_id"] == item]

    roles.set_away(conn, "rune", "ines", now + timedelta(days=1), delegate="olav")
    conn.commit()
    item = case(conn, "Close the drain valve")
    engine.dispatch_item(conn, item, "ines", "rune")
    conn.commit()
    assert not [n for n in told(conn, "rune") if n["item_id"] == item]
    n = next(n for n in told(conn, "olav") if n["item_id"] == item)
    assert n["text"].endswith("(You cover for rune.)")
    roles.set_away(conn, "rune", "ines", None)
    conn.commit()


def test_a_step_coming_due_tells_its_holder_once(conn):
    item = case(conn, "Swap the filter", assignee="pia", due=engine.utcnow() + timedelta(minutes=20))
    due = [n for n in told(conn, "pia") if n["item_id"] == item and n["kind"] == "due_soon"]
    assert len(due) == 1 and "is due in" in due[0]["text"]
    assert len([n for n in told(conn, "pia") if n["item_id"] == item and n["kind"] == "due_soon"]) == 1


def test_an_escalation_tells_the_new_holder_and_whoever_had_it(conn):
    item = engine.start_or_correlate(conn, "upset", {"incident_id": "n-1", "title": "Upset n-1",
                                                     "subjects": ["fz-unit-1"]}, "agent:sre")["item_id"]
    conn.commit()
    step = conn.execute("select id from work_token where item_id = %s and kind = 'user' and state = 'waiting' "
                        "order by id limit 1", (item,)).fetchone()["id"]
    engine.hand_off(conn, step, "ines", "olav")
    conn.commit()
    engine.tick(conn, now=engine.utcnow() + timedelta(days=30))
    mine = [n for n in told(conn, "olav") if n["item_id"] == item and n["kind"] == "escalated"]
    assert mine and "escalated away from you" in mine[0]["text"]

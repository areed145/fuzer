"""People's notes on a report: pinned to a report, scope and period, with live measure values, changed only by their
author, and shown on the longer periods that hold them."""

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.api import commentary
from fuzer.ops import report_notes as rn

pytestmark = pytest.mark.integration

DAY = date(2026, 11, 24)


def _id(conn, label):
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_a_note_shows_on_its_own_period_and_the_longer_ones_that_hold_it(okan_db):
    conn = okan_db.conn
    from fuzer.core.pack import current_pack

    pack = current_pack()
    tue = rn.add(conn, "work", None, "1d", date(2026, 11, 18), "Low on Tuesday: the compressor tripped.", "user:ana")
    week = rn.add(conn, "work", None, "7d", DAY, "A quiet week.", "user:ben")
    rn.add(conn, "work", None, "1d", date(2026, 11, 10), "Outside the week.", "user:ana")
    rn.add(conn, "other", None, "7d", DAY, "Another report.", "user:ana")

    weekly = rn.for_period(conn, pack, "work", None, "7d", DAY)
    assert [n["id"] for n in weekly] == [week["id"], tue["id"]]
    assert weekly[0]["own"] and not weekly[1]["own"] and weekly[1]["start"] == "2026-11-18"
    assert [n["id"] for n in rn.for_period(conn, pack, "work", None, "1d", date(2026, 11, 18))] == [tue["id"]]
    assert rn.for_period(conn, pack, "work", "fz-elsewhere", "7d", DAY) == []


def test_only_the_author_changes_a_note_and_a_removed_one_is_kept(okan_db):
    conn = okan_db.conn
    n = rn.add(conn, "work", None, "7d", DAY, "First go.", "user:ana")
    with pytest.raises(rn.NotYours):
        rn.edit(conn, "work", n["id"], "Not mine.", "user:ben")
    assert rn.edit(conn, "work", n["id"], "Second go.", "user:ana")["edited_at"]
    with pytest.raises(rn.NoteError):
        rn.edit(conn, "work", n["id"], "   ", "user:ana")
    rn.remove(conn, "work", n["id"], "user:ana")
    assert n["id"] not in [x["id"] for x in rn.for_period(conn, None, "work", None, "7d", DAY)]
    assert conn.execute("select deleted_at from report_note where id = %s", (n["id"],)).fetchone()["deleted_at"]
    with pytest.raises(rn.NoteError):
        rn.add(conn, "work", None, "2w", DAY, "Bad window.", "user:ana")


def test_live_values_are_the_measure_for_the_scope_and_period(okan_db):
    from fuzer.core import metrics
    from fuzer.core.pack import current_pack

    conn = okan_db.conn
    pack = current_pack()
    area = _id(conn, "OKAN-20")
    body = "Urgent overdue {maintenance.urgent_overdue}, at the field {maintenance.urgent_overdue@facility}, " \
           "unknown {no.such_measure}, nowhere {maintenance.urgent_overdue@pump}."
    v = rn.values(conn, pack, body, area, "7d", DAY)
    want = metrics.value(conn, pack, "maintenance.urgent_overdue", scope=area, window="7d")
    assert v["{maintenance.urgent_overdue}"]["value"] == want["value"]
    assert v["{maintenance.urgent_overdue}"]["unit"] == "orders"
    field = v["{maintenance.urgent_overdue@facility}"]
    assert field["error"] is None and field["scope"] != area
    assert "no metric" in v["{no.such_measure}"]["error"]
    assert v["{maintenance.urgent_overdue@pump}"]["error"] == "the scope sits in no pump"


def test_the_routes_bind_the_author_and_refuse_someone_elses_note(okan_db, monkeypatch):
    app = FastAPI()
    app.include_router(commentary.router)
    client = TestClient(app)
    r = client.post("/reports/work/notes", json={"body": "From the route.", "window": "7d", "at": "2026-11-24"})
    assert r.status_code == 200, r.text
    nid = r.json()["id"]
    assert client.post("/reports/no-such-report/notes", json={"body": "x", "window": "7d"}).status_code == 404
    assert client.post("/reports/work/notes", json={"body": "x", "window": "2w"}).status_code == 422
    okan_db.conn.execute("update report_note set by_user = 'user:someone-else' where id = %s", (nid,))
    okan_db.conn.commit()
    assert client.patch(f"/reports/work/notes/{nid}", json={"body": "mine now"}).status_code == 403
    assert client.delete(f"/reports/work/notes/{nid}").status_code == 403

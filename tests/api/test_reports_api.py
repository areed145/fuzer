"""Report pages (docs/reports.md): every panel of every report answers from the application's own model for a scope
and window, and a link that names a report, scope or window that isn't there is refused with why."""

import pytest
from fastapi.testclient import TestClient

from fuzer.core import reports

pytestmark = pytest.mark.sequence


def test_every_panel_of_every_report_answers_on_okan(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        listed = client.get("/reports").json()
        assert {"work"} <= {r["key"] for r in listed}
        for r in listed:
            view = client.get(f"/reports/{r['key']}").json()
            scope, window = view["scope"], view["window"]
            assert scope["kind"] in r["scope"]["kinds"] and window["key"] == r["window"]["default"]
            assert view["scopes"] and view["scopes"][0]["id"] == scope["id"]
            for p in reports.panels(view["definition"]):
                if p["type"] in ("note", "summary"):
                    continue
                got = client.get(f"/reports/{r['key']}/panels/{p['id']}",
                                 params={"scope": scope["id"], "window": window["key"]})
                assert got.status_code == 200, got.text
                body = got.json()
                assert not body.get("error"), (p["id"], body.get("error"))
                assert body["title"] and body["window"]["key"] == window["key"] and body["scope"]["id"] == scope["id"]
                assert isinstance(body["scoped"], bool) and body["href"]
                if not body["scoped"]:  # a figure for the whole register says so
                    assert "whole register" in (body["note"] or "").lower(), p["id"]


def test_the_work_report_reads_the_registered_numbers_and_the_work_list(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        view = client.get("/reports/work").json()
        fid = view["scope"]["id"]
        stat = client.get("/reports/work/panels/backlog", params={"scope": fid}).json()
        metric = client.get("/metrics/work.backlog").json()
        assert stat["value"] == metric["value"] and stat["unit"] == metric["unit"]  # one value, one source
        cases = client.get("/reports/work/panels/overdue", params={"scope": fid}).json()
        listed = client.get("/cases", params={"within": fid, "overdue": "true", "sort": "due"}).json()
        assert cases["total"] == listed["total"] and cases["href"].startswith("/list?")
        assert [r["uid"] for r in cases["cases"]] == [r["uid"] for r in listed["rows"][:8]]


def test_a_report_link_that_cant_be_read_is_refused(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        assert client.get("/reports/no-such").status_code == 404
        assert client.get("/reports/work", params={"window": "1d"}).status_code == 400  # not one of its windows
        assert client.get("/reports/work", params={"scope": "fz-no-such"}).status_code == 400
        assert client.get("/reports/work/panels/no-such").status_code == 400

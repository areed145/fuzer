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
        assert {"work", "operations", "production", "safety", "plan", "data"} <= {r["key"] for r in listed}
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
                # an analysis and the daily report draw from their own reads; every figure drills somewhere
                assert isinstance(body["scoped"], bool) and (body["href"] or p["type"] in ("view", "narrative"))
                if not body["scoped"]:  # a figure for the whole register says so
                    assert "whole register" in (body["note"] or "").lower(), p["id"]


def test_the_work_report_reads_the_registered_numbers_and_the_work_list(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        view = client.get("/reports/work").json()
        fid = view["scope"]["id"]
        stat = client.get("/reports/work/panels/backlog", params={"scope": fid}).json()
        metric = client.get("/metrics/work.backlog", params={"scope": fid, "window": view["window"]["key"]}).json()
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


def test_daily_operations_ends_on_the_last_production_day_and_draws_its_daily_report(okan_db):
    from fuzer import api
    from fuzer.core import metrics

    with TestClient(api.app) as client:
        view = client.get("/reports/operations").json()
        day = metrics.cutoff_day(okan_db.conn, "production_day").isoformat()
        assert view["window"]["at"] == day and view["window"]["asked"] and view["window"]["key"] == "1d"
        oil = client.get("/reports/operations/panels/oil").json()
        assert oil["window"]["at"] == day and str(oil["as_of"])[:10] == day
        story = client.get("/reports/operations/panels/narrative").json()
        assert story["day"] == day and story["type"] == "narrative"
        # a day not closed says so, and who closes it, never a developer command (tour D6)
        open_day = client.get("/reports/operations/panels/narrative", params={"at": "2020-01-01"}).json()
        assert open_day["status"] is None and open_day["note"].startswith("Not closed yet"), open_day
        assert "`" not in open_day["note"] and "fuzer report" not in open_day["note"]
        named = client.get("/reports/operations", params={"at": "2025-12-01"}).json()
        assert named["window"]["at"] == "2025-12-01"


def test_a_table_of_several_measures_has_a_column_for_each(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        t = client.get("/reports/data/panels/datasets").json()
        assert [c["label"] for c in t["columns"]][1:] == ["Source completeness", "Source timeliness",
                                                           "Source agreement", "Source validity"]
        assert t["rows"] and all(set(r["cells"]) - {"label"} <= {"m0", "m1", "m2", "m3"} for r in t["rows"])
        assert not t["scoped"] and "whole register" in t["note"].lower()

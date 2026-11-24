"""Custom reports (docs/reports.md, "Custom reports"): a copy of any report or a blank one, kept per account and
private until shared, changed on the page and saved as a new version each time, checked as a standard report is,
and brought back to any earlier version."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.sequence

ANA, BEN = "user:ana", "user:ben"


def test_a_copy_keeps_its_origin_and_draws_like_the_standard_report(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        made = client.post("/reports/work/copy", json={"by": ANA})
        assert made.status_code == 200, made.text
        mine = made.json()
        key = mine["key"]
        assert key.startswith("custom-") and mine["mine"] and mine["shared"] == "" and mine["version"] == 1
        assert mine["origin"]["key"] == "work" and mine["origin"]["copied_version"] == mine["origin"]["version"]
        view = client.get(f"/reports/{key}", params={"by": ANA}).json()
        assert view["definition"]["source"] == "custom" and view["custom"]["mine"]
        std = client.get("/reports/work").json()
        assert view["scope"]["id"] == std["scope"]["id"]
        a = client.get(f"/reports/{key}/panels/backlog", params={"by": ANA, "scope": std["scope"]["id"]}).json()
        b = client.get("/reports/work/panels/backlog", params={"scope": std["scope"]["id"]}).json()
        assert a["value"] == b["value"]  # one value, one source
        # private: listed for its maker only, and not there at all for anyone else
        assert key in {r["key"] for r in client.get("/reports", params={"by": ANA}).json()}
        assert key not in {r["key"] for r in client.get("/reports", params={"by": BEN}).json()}
        assert client.get(f"/reports/{key}", params={"by": BEN}).status_code == 404
        assert client.get(f"/reports/{key}/panels/backlog", params={"by": BEN}).status_code == 404


def test_saving_bumps_the_version_checks_it_and_restores(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        key = client.post("/reports/custom", json={"title": "My morning", "by": ANA}).json()["key"]
        view = client.get(f"/reports/{key}", params={"by": ANA}).json()
        assert view["definition"]["rows"] == []  # blank
        d = view["definition"]
        d["rows"] = [{"panels": [{"id": "b", "type": "stat", "measure": "work.backlog"}]}]
        saved = client.put(f"/reports/{key}", json={"definition": d, "base": 1, "by": ANA})
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == 2
        # an unknown measure is refused with the reason, and nothing is kept
        bad = {**d, "rows": [{"panels": [{"id": "x", "type": "stat", "measure": "no.such"}]}]}
        refused = client.put(f"/reports/{key}", json={"definition": bad, "base": 2, "by": ANA})
        assert refused.status_code == 400 and "no measure 'no.such'" in refused.json()["detail"]
        # editing from an old version is refused, not merged over
        assert client.put(f"/reports/{key}", json={"definition": d, "base": 1, "by": ANA}).status_code == 409
        # the definition view: YAML text, saved back as text
        text = client.get(f"/reports/{key}/definition", params={"by": ANA}).json()["text"]
        assert "work.backlog" in text and "key:" not in text
        assert text.index("id: b") < text.index("type: stat") < text.index("size:")  # a panel reads id, type, size
        renamed = client.put(f"/reports/{key}", json={"text": text.replace("My morning", "Morning call"), "base": 2,
                                                      "by": ANA}).json()
        assert renamed["title"] == "Morning call" and renamed["version"] == 3
        checked = client.post("/reports/check", json={"text": "title: x\nscope: {kinds: [nowhere]}\nrows: []"})
        assert checked.status_code == 400 and "nowhere" in checked.json()["detail"]
        assert "custom-0" not in checked.json()["detail"]  # a definition not saved yet isn't named by a made-up key
        assert client.post("/reports/check", json={"text": "title: [unclosed"}).status_code == 400
        # history and restore: restoring is a new version, the ones between stay kept
        versions = client.get(f"/reports/{key}/versions", params={"by": ANA}).json()
        assert [v["version"] for v in versions] == [3, 2, 1] and versions[0]["current"]
        back = client.post(f"/reports/{key}/versions/1/restore", params={"by": ANA}).json()
        assert back["version"] == 4 and back["title"] == "My morning"
        assert client.get(f"/reports/{key}", params={"by": ANA}).json()["definition"]["rows"] == []
        # only its owner changes it
        assert client.put(f"/reports/{key}", json={"definition": d, "by": BEN}).status_code == 404
        assert client.put("/reports/work", json={"definition": d, "by": ANA}).status_code == 403


def test_a_draft_panel_reads_before_it_is_saved(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        key = client.post("/reports/custom", json={"title": "Draft", "by": ANA}).json()["key"]
        d = client.get(f"/reports/{key}", params={"by": ANA}).json()["definition"]
        d["rows"] = [{"panels": [{"id": "new", "type": "stat", "measure": "work.backlog"}]}]
        got = client.post(f"/reports/{key}/draft/panels/new", json={"definition": d, "by": ANA})
        assert got.status_code == 200, got.text
        assert got.json()["type"] == "stat" and "value" in got.json()
        assert client.get(f"/reports/{key}", params={"by": ANA}).json()["definition"]["rows"] == []  # nothing saved


def test_sharing_with_everyone_or_a_role(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        key = client.post("/reports/work/copy", json={"title": "Shared work", "by": ANA}).json()["key"]
        assert client.post(f"/reports/{key}/share", json={"shared": "role:no-such", "by": ANA}).status_code == 400
        shared = client.post(f"/reports/{key}/share", json={"shared": "everyone", "by": ANA}).json()
        assert shared["shared"] == "everyone"
        theirs = {r["key"]: r for r in client.get("/reports", params={"by": BEN}).json()}
        assert key in theirs and not theirs[key]["mine"]
        assert client.get(f"/reports/{key}", params={"by": BEN}).json()["custom"]["mine"] is False
        # a reader copies it to change it; the copy's origin is the shared report
        copy = client.post(f"/reports/{key}/copy", json={"by": BEN}).json()
        assert copy["origin"]["key"] == key and copy["mine"]
        assert client.post(f"/reports/{key}/share", json={"shared": "", "by": BEN}).status_code == 403
        assert client.post(f"/reports/{key}/share", json={"shared": "", "by": ANA}).json()["shared"] == ""
        assert client.get(f"/reports/{key}", params={"by": BEN}).status_code == 404
        assert client.delete(f"/reports/{key}", params={"by": ANA}).json() == {"ok": True}
        assert client.get(f"/reports/{key}", params={"by": ANA}).status_code == 404


def test_the_model_offers_what_fits_the_scope(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        m = client.get("/model", params={"kinds": "area"}).json()
        keys = {x["key"]: x for x in m["measures"]}
        assert "work.backlog" in keys and m["row_sets"] and "stat" in m["types"]
        assert all("facility" not in x["grains"] and "area" not in x["grains"] for x in m["measures"])
        everything = {x["key"]: x for x in client.get("/model").json()["measures"]}
        assert any("facility" in x["grains"] for x in everything.values())

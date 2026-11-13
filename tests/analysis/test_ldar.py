"""LDAR survey evidence (#371): each survey's screening, the repeat leaker, and coverage with the equipment no survey
reached reported as unknown, on Okan's quarterly Method 21 surveys."""

import json

from fuzer.analysis import ldar


def test_surveys_find_the_repeat_leaker_and_name_the_unsurveyed_as_unknown(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "ldar.json").read_text())
    out = ldar.surveys(conn)
    assert len(out["surveys"]) == 8 and out["verdict"] == "repeat_leakers"
    top = out["repeat_leakers"][0]
    assert (top["tag"], top["component"]) == (key["repeat"]["tag"], key["repeat"]["component"])
    assert top["surveys"] == key["repeat"]["surveys"] and top["consecutive"] == 3
    assert len(out["repeat_leakers"]) == 1  # the other leaks were repaired before the next survey
    unknown = {u["tag"] for u in out["coverage"]["unknown"]}
    assert unknown == set(key["unsurveyed"]) and all(u["last_survey"] is None for u in out["coverage"]["unknown"])
    assert out["coverage"]["covered"] + len(unknown) == out["coverage"]["in_scope"]
    skipped = next(s for s in out["surveys"] if s["date"] == key["skipped"]["survey"])
    assert set(skipped["missed"]) == set(key["skipped"]["tags"])
    assert "unknown, not clean" in out["note"]
    conn.rollback()


def test_no_surveys_means_unknown_not_clean(okan_db):
    conn = okan_db.conn
    out = ldar.surveys(conn, since="2023-01-01", until="2023-12-31")
    assert out["verdict"] == "no_surveys" and "unknown, not absent" in out["note"]
    conn.rollback()


def test_the_chart_and_the_card_reach_people_and_agents(okan_db):
    import pytest
    from fastapi.testclient import TestClient

    from fuzer import api
    from fuzer.plots import ChartError, troubleshooting

    conn = okan_db.conn
    fig = troubleshooting.ldar_leaks(conn)
    assert sum(fig.data[1]["y"]) == 3 and fig.summary["coverage"]["unknown"]
    with pytest.raises(ChartError, match="unknown, not absent"):
        troubleshooting.ldar_leaks(conn, since="2023-01-01", until="2023-12-31")
    r = TestClient(api.app).get("/plant/ldar")
    assert r.status_code == 200 and r.json()["verdict"] == "repeat_leakers"
    conn.rollback()

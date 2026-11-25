"""What a site hasn't loaded is unknown, never none. Okan's shared test plant has no bypass register and no business
plan: the measures, the report's panels, the AI note's facts and the shift handover say so and why, and Plan against
actual says it in one line instead of empty panels. Once a bypass register is loaded, nothing in place reads 0."""

from datetime import UTC, date, datetime

import pytest
from psycopg.types.json import Jsonb

from fuzer.agents import report_summary
from fuzer.core import metrics, rowsets
from fuzer.core.measures import builtin
from fuzer.core.pack import current_pack
from fuzer.ops import business_plan, handover, report_pages, safeguards

pytestmark = pytest.mark.integration

DAY = date(2025, 12, 31)


def test_the_bypass_measures_say_the_register_s_missing_text():
    for key in ("safety.bypasses_active", "safety.bypass_age"):
        assert builtin()[key]["from"]["missing"] == safeguards.NO_REGISTER
    assert rowsets.builtin()["bypasses.active"]["from"]["missing"] == safeguards.NO_REGISTER


def test_without_a_bypass_register_every_place_says_unknown_and_why(okan_db):
    conn, pack = okan_db.conn, current_pack()
    assert not safeguards.loaded(conn)
    v = metrics.value(conn, pack, "safety.bypasses_active", window="1d", at=DAY)
    assert v["value"] is None and v["unknown"] and v["note"] == safeguards.NO_REGISTER

    stat = report_pages.panel_data(conn, pack, "safety", "bypasses", window="30d", at=DAY)
    assert stat["value"] is None and stat["unknown"] and stat["note"] == safeguards.NO_REGISTER
    table = report_pages.panel_data(conn, pack, "safety", "bypass-list", window="30d", at=DAY)
    assert table["rows"] == [] and table["unknown"] and table["note"] == safeguards.NO_REGISTER
    incidents = report_pages.panel_data(conn, pack, "safety", "incidents", window="30d", at=DAY)
    assert incidents["unknown"] is False

    facts = report_summary.facts(conn, pack, "safety", None, "30d", DAY)
    said = {p["id"]: p["result"] for p in facts["panels"]}
    assert said["bypasses"]["unknown"] is True and said["bypasses"]["note"] == safeguards.NO_REGISTER
    assert "unknown" not in said["incidents"]  # said only where it is
    assert "never read it as zero" in report_summary.SYSTEM

    f = handover.facts(conn, datetime(2025, 12, 30, 18, tzinfo=UTC), datetime(2025, 12, 31, 6, tzinfo=UTC))
    assert f["unknown"] == {"Active bypasses": safeguards.NO_REGISTER} and f["sections"]["Active bypasses"] == []


def test_a_loaded_register_with_nothing_in_place_reads_zero(okan_db):
    conn, pack = okan_db.conn, current_pack()
    tag = conn.execute("select e.fuzer_id from entity e join entity_label l using (fuzer_id) "
                       "where e.kind = 'tag' and l.label = '20-K-2200B'").fetchone()
    tag = tag["fuzer_id"] if tag else conn.execute("select fuzer_id from entity where kind = 'tag' limit 1").fetchone()["fuzer_id"]
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-bypass-test', 'bypass')")
    for field, value in (("state", "removed"), ("bypassed_from", "2025-12-01T08:00")):
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-bypass-test', %s, %s, "
                     "'safeguards', 't')", (field, Jsonb(value)))
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-bypass-test', 'bypasses', %s, "
                 "'{safeguards}', 1)", (tag,))
    try:
        assert safeguards.loaded(conn)
        v = metrics.value(conn, pack, "safety.bypasses_active", window="1d", at=DAY)
        assert v["value"] == 0 and not v["unknown"]
        stat = report_pages.panel_data(conn, pack, "safety", "bypasses", window="30d", at=DAY)  # the facility's scope
        assert stat["value"] == 0 and not stat["unknown"]
        age = metrics.value(conn, pack, "safety.bypass_age", window="1d", at=DAY)
        assert age["value"] is None and not age["unknown"] and age["note"] == "none open at the window's end"
        f = handover.facts(conn, datetime(2025, 12, 30, 18, tzinfo=UTC), datetime(2025, 12, 31, 6, tzinfo=UTC))
        assert f["unknown"] == {}
        conn.execute("update golden set value = %s where fuzer_id = 'fz-bypass-test' and field = 'state'", (Jsonb("active"),))
        assert metrics.value(conn, pack, "safety.bypasses_active", window="1d", at=DAY)["value"] == 1
    finally:
        conn.rollback()


def test_without_a_business_plan_the_report_says_so_in_one_line(okan_db):
    conn, pack = okan_db.conn, current_pack()
    assert not business_plan.loaded(conn)
    v = report_pages.view(conn, pack, "plan", window="qtd", at=DAY)
    assert v["unknown"] == [{"rows": ["headline", "per-program", "lines"], "note": "No business plan loaded"}]
    for panel in ("est-hours", "act-cost", "unplanned", "plan-lines"):
        r = report_pages.panel_data(conn, pack, "plan", panel, window="qtd", at=DAY)
        assert r["unknown"] and r["note"] == business_plan.MISSING, panel
    assert report_pages.view(conn, pack, "work", window="7d", at=DAY)["unknown"] == []

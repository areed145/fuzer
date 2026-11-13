"""P3.7a (#936): the LDAR source pack brings work. A component found leaking in consecutive surveys and still leaking at
its latest screening is work for the engineer until it screens clean. On Okan, 20-K-2300A's dry gas seal vent leaks in
the May, August and November 2025 surveys; every other leak was repaired before the next survey."""

import pytest

from fuzer.core import detectors
from fuzer.core.pack import load_pack
from fuzer.ops import emissions
from fuzer.sources import packs

PACK = load_pack("okan")
KEY = "20-K-2300A dry gas seal vent"


@pytest.mark.unit
def test_the_ldar_and_ec_packs_bring_their_detectors_and_work_types():
    import yaml

    pack = yaml.safe_load(packs.builtin("ldar"))
    assert packs.detectors_of(pack) == ["repeat_leaks"]  # switched on when the pack is installed
    assert detectors.get("repeat_leaks").raises == (emissions.REPEAT_TYPE,)
    assert "gas_balance_gaps" in packs.detectors_of(yaml.safe_load(packs.builtin("ec")))  # the daily report's pack
    assert detectors.get("gas_balance_gaps").raises == (emissions.GAP_TYPE,)


@pytest.mark.integration
def test_a_leak_found_in_consecutive_surveys_is_work_until_it_screens_clean(okan_db):
    conn = okan_db.conn
    try:
        assert emissions.watch_repeat_leaks(conn, now="2025-05-20")["raised"] == []  # leaking once: a repair to do
        out = emissions.watch_repeat_leaks(conn, now="2025-08-14")
        assert [r["leak"] for r in out["raised"]] == [KEY] and out["raised"][0]["new"], out
        x = conn.execute("select * from exception_record where type = 'repeat_leak' and key = %s", (KEY,)).fetchone()
        assert x["state"] == "open" and x["item_id"] and x["title"].endswith("leaking in 2 surveys in a row")
        again = emissions.watch_repeat_leaks(conn, now="2025-11-13")
        assert [r["new"] for r in again["raised"]] == [False]  # the same work, not a second case

        # the repair holds: a clean screening after the November survey clears it
        tag = emissions.still_leaking(conn, "2025-11-13")[0]["id"]
        conn.execute("insert into measurement (fuzer_id, quantity, at, value, unit, location, method, record) "
                     "values (%s, 'leak_screening', '2025-11-20', 35, 'ppm', 'dry gas seal vent', 'Method 21', 'test:ldar')",
                     (tag,))
        done = emissions.watch_repeat_leaks(conn, now="2025-11-21")
        assert done["raised"] == [] and done["cleared"] == [KEY]

        assert detectors.run(conn, PACK, "repeat_leaks", now="2025-08-14")["raised"]  # as a schedule runs it
    finally:
        conn.rollback()
        conn.execute("delete from measurement where record = 'test:ldar' and quantity = 'leak_screening'")
        conn.commit()


@pytest.mark.integration
def test_a_gas_balance_open_for_days_is_work_until_it_closes(okan_db):
    """The GGCP fuel gas meter reads zero from 15 September to 8 October 2025 while the trains keep burning fuel."""
    conn = okan_db.conn
    try:
        assert emissions.watch_gas_balance(conn, now="2025-09-10")["raised"] == []
        assert emissions.watch_gas_balance(conn, now="2025-09-16")["raised"] == []  # two days open: not yet
        out = emissions.watch_gas_balance(conn, now="2025-09-17")  # the third day's report
        assert [r["gap"] for r in out["raised"]] == ["gas balance · 2025-09-15"] and out["raised"][0]["new"], out
        x = conn.execute("select * from exception_record where type = 'gas_balance_gap'").fetchone()
        assert x["state"] == "open" and x["subjects"] and "fuel reading zero" in x["title"], x["title"]
        assert emissions.watch_gas_balance(conn, now="2025-10-01")["raised"][0]["new"] is False
        done = emissions.watch_gas_balance(conn, now="2025-10-12")  # the meter mended on the 9th
        assert done["raised"] == [] and done["cleared"] == ["gas balance · 2025-09-15"]
    finally:
        conn.rollback()

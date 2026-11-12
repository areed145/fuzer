"""P3.1c (#341): a gas-lifted well heading, its casing pressure surging regularly, is raised for the production
engineer as a candidate; on Okan, the first gas-lifted well the fake plant leaves alone heads in late December 2025."""

import json
import time

import pytest

from fuzer.analysis import gas_lift
from fuzer.core import detectors
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_a_heading_well_is_raised_until_its_casing_settles(conn):
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    t0 = time.monotonic()
    out = detectors.run(conn, PACK, "gas_lift_instability", until="2025-12-20T00:00")
    took = time.monotonic() - t0
    assert not out["errors"] and len(out["raised"]) == 1, out  # the one heading well, not the field
    x = conn.execute("select * from exception_record where type = 'gas_lift_instability' and state = 'open'").fetchone()
    assert "heading on gas lift" in x["title"] and "every 75" in x["title"], x["title"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == \
        "production_engineer"
    said = json.dumps(x["evidence"])
    assert "not a diagnosis" in said and "synthetic values" in said
    assert took < 60, took
    before = gas_lift.unstable(conn, until="2025-12-10T00:00")
    assert before == [], before  # steady before it starts heading
    after = detectors.run(conn, PACK, "gas_lift_instability", until="2026-01-01T12:00")  # settled
    assert after["cleared"] == [x["key"]] and not after["raised"], after
    conn.commit()

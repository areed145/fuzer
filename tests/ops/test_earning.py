"""How detection and agents are performing (P7.4, #955): per detector, its cases and close-outs, hours from what it
found to the case, the value it confirmed and how its confirmed cases were found (P7.4a)."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, work
from fuzer.core.pack import load_pack
from fuzer.ops import disposition, earning

PACK = load_pack("okan")
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.commit()
        yield c


def _case(conn, key: str, finding: str, closed_as: str | None = None, *, origin: str | None = "deterministic",
          hours_before: float = 2, value: float | None = None) -> int:
    item = work.open_case(conn, "manual", f"{finding} ({key})", by="fuzer")["id"]
    rec = {"detector": finding, "start": (NOW - timedelta(hours=hours_before)).isoformat()}
    if origin:
        rec["origin"] = origin
    conn.execute("insert into exception_record (type, key, title, evidence, item_id, value_usd) values "
                 "('upset', %s, %s, %s, %s, %s)", (key, key, Jsonb({"finding": rec}), item, value))
    if closed_as:
        disposition.record(conn, PACK, item, "ana", closed_as,
                           **({"duplicate_of": item - 1} if closed_as == "duplicate" else {}),
                           **({"failure_mode": "VIB", "cause": "3", "activity": "1"} if closed_as == "real" else {}))
    return item


@pytest.mark.integration
def test_each_detector_shows_whether_it_earns_its_keep(conn):
    try:
        for i, (origin, value) in enumerate([("deterministic", 120000.0), ("deterministic", 30000.0),
                                             ("deterministic", None), ("deterministic", None), ("agentic", None)]):
            _case(conn, f"s{i}", "sre.steps", "real", origin=origin, value=value, hours_before=1 + i)
        _case(conn, "s5", "sre.steps", "false_alarm")
        for i, how in enumerate(["false_alarm", "false_alarm", "false_alarm", "duplicate", "duplicate", "real"]):
            _case(conn, f"d{i}", "sre.drifts", how, origin=None if how == "real" else "deterministic")
        _case(conn, "x0", "sre.excursions", "real", origin="external")
        _case(conn, "x1", "sre.excursions")  # still open
        conn.commit()

        got = earning.detectors(conn)
        by = {r["detector"]: r for r in got["detectors"]}
        steps, drifts, exc = by["steps"], by["drifts"], by["excursions"]
        assert [r["detector"] for r in got["detectors"]] == ["drifts", "steps", "excursions"]  # the most cases first
        assert (steps["cases"], steps["closed"], steps["real"], steps["verdict"]) == (6, 6, 5, "earning")
        assert steps["value_confirmed_usd"] == 150000.0 and steps["valued"] == 2  # the false alarm's value isn't counted
        assert steps["by_origin"] == {"deterministic": 4, "agentic": 1, "external": 0, "unknown": 0}
        assert 2.5 <= steps["hours_to_case"] <= 3.6, steps["hours_to_case"]  # the median of 1 to 5 hours, and the 2
        assert steps["title"] and steps["kind"] == "statistical" and len(steps["item_ids"]) == 6

        assert (drifts["noise"], drifts["closed"], drifts["verdict"]) == (5, 6, "needs_tuning")
        assert drifts["by_origin"]["unknown"] == 1 and drifts["value_confirmed_usd"] is None
        assert (exc["cases"], exc["closed"], exc["verdict"]) == (2, 1, "too_few")

        assert got["confirmed"] == 7
        assert got["by_origin"]["deterministic"] == {"cases": 4, "share": round(4 / 7, 3)}
        assert got["by_origin"]["agentic"]["cases"] == got["by_origin"]["external"]["cases"] == 1
    finally:
        conn.rollback()

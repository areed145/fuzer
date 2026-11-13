"""Detectors tune from dispositions (P6.3, #944): close-outs put down to the detector and equipment class behind each
case (P6.3a, #945)."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, work
from fuzer.core.pack import load_pack
from fuzer.ops import disposition, tuning

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        for unit, cls in (("fz-tag-pump01", "PU"), ("fz-tag-comp01", "CO")):
            c.execute("insert into entity (fuzer_id, kind) values (%s, 'tag'), (%s, 'taxonomy_class')",
                      (unit, f"fz-cls-{cls}"))
            c.execute("insert into entity_label values (%s, %s)", (f"fz-cls-{cls}", f"ISO14224:{cls}.XX"))
            c.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'classified_as', %s, "
                      "'{test}', 1)", (unit, f"fz-cls-{cls}"))
        c.commit()
        yield c


def _case(conn, key: str, finding: str, work_type: str, unit: str, closed_as: str | None = None) -> int:
    item = work.open_case(conn, "manual", f"{finding} on {unit} ({key})", by="fuzer")["id"]
    conn.execute("insert into exception_record (type, key, title, subjects, evidence, item_id) values "
                 "(%s, %s, %s, %s, %s, %s)", (work_type, key, key, [unit], Jsonb({"finding": {"detector": finding}}), item))
    if closed_as:
        disposition.record(conn, PACK, item, "ana", closed_as,
                           **({"duplicate_of": item - 1} if closed_as == "duplicate" else {}),
                           **({"failure_mode": "VIB", "cause": "3", "activity": "1"} if closed_as == "real" else {}))
    return item


def test_a_case_is_put_down_to_the_detector_behind_it():
    assert tuning.detector_of("sre.steps", "upset") == "steps"  # the SRE names its first detection's detector
    assert tuning.detector_of("loops.oscillation", "loop_candidate") == "loop_oscillation"  # the one that raises it
    assert tuning.detector_of("sre.upset", "upset") is None  # every SRE detector raises upsets: none to blame
    assert tuning.detector_of(None, "manual") is None


def test_close_outs_count_false_alarms_and_duplicates_as_noise_per_detector_and_class(conn, monkeypatch):
    _case(conn, "a", "sre.steps", "upset", "fz-tag-pump01", "real")
    _case(conn, "b", "sre.steps", "upset", "fz-tag-pump01", "false_alarm")
    _case(conn, "c", "sre.steps", "upset", "fz-tag-pump01", "duplicate")
    _case(conn, "d", "sre.steps", "upset", "fz-tag-comp01", "data_problem")
    _case(conn, "e", "sre.steps", "upset", "fz-tag-comp01")  # still open
    _case(conn, "f", "sre.drifts", "upset", "fz-tag-comp01", "false_alarm")
    _case(conn, "g", "sre.upset", "upset", "fz-tag-comp01", "false_alarm")  # no detector to put it down to
    conn.commit()

    got = tuning.closeouts(conn)
    steps = got["steps"]
    assert (steps["cases"], steps["closed"], steps["real"], steps["false_alarm"], steps["duplicate"],
            steps["data_problem"], steps["noise"]) == (5, 4, 1, 1, 1, 1, 2)
    assert steps["noise_rate"] == 0.5 and steps["confirmed_rate"] == 0.25
    by = {r["class"]: r for r in steps["by_class"]}
    assert set(by) == {"PU", "CO"}
    assert (by["PU"]["closed"], by["PU"]["noise_rate"]) == (3, 2 / 3)
    assert (by["CO"]["cases"], by["CO"]["closed"], by["CO"]["noise"]) == (2, 1, 0)
    assert got["drifts"]["false_alarm"] == 1
    assert sum(r["cases"] for r in got.values()) == 6

    from fuzer.api import settings as settings_api  # what the Detection page reads

    monkeypatch.setattr(settings_api, "pack", lambda: PACK)
    monkeypatch.setattr(settings_api.settings, "project", lambda: None)

    page = {d["name"]: d for d in settings_api.detectors_list(conn)["detectors"]}
    assert page["steps"]["closeouts"]["noise"] == 2 and page["excursions"]["closeouts"] == tuning.empty()
    conn.rollback()

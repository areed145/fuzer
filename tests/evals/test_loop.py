"""The loop acceptance eval (P1.7): every type of work on a bundle goes arrive to counted, and a type that can't
says at which step and why."""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fuzer.evals import loop

# Each field type's job as the loop eval gave it to the phone, which the browser test renders at 390 px.
# FUZER_WRITE_FIXTURES=1 rewrites it from an Okan run.
JOBS = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures" / "loop-field-jobs.json"


def _json(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else float(x) if isinstance(x, Decimal) else str(x)


OPTIONAL_ITEM = {"unit"}  # core.findings.Item: a unit is written only when the value has one


def _shape(job: dict) -> dict:
    return {"keys": sorted(job), "finding": sorted(job["finding"]),
            "item": sorted({k for i in job["finding"]["items"] for k in i} - OPTIONAL_ITEM),
            "asset": sorted({k for a in job["assets"] for k in a})}


def test_every_type_of_work_on_okan_completes_the_loop(okan_db):
    res = loop.run(okan_db.conn)
    done = {r["type"]: r for r in res["types"]}
    assert {"upset", "iow_breach", "sap_notification", "machine_trip", "machine_health", "records_gap",
            "source_issue"} <= set(done), sorted(done)  # every routed type, declared before any was raised
    assert not res["failed"], "\n".join(res["failed"])
    assert all(list(r["steps"]) == list(loop.STEPS) for r in res["types"])
    # Gate A: the work comes in from its own detector or source, as in production, not from the loop (P1.7c)
    arrived = {r["type"]: r["arrived"] for r in res["types"]}
    assert not {"upset", "iow_breach", "sap_notification", "machine_trip", "machine_health", "records_gap",
                } & set(res["synthetic"]), arrived
    # the register build's questions, raised before the processes loaded, run them once they do, as on the API's
    # start (routing.adopt_open_cases); work about a source matches one of the project's sources (#747)
    assert not {"conflicting_sources", "context_gap", "criticality_not_assessed", "drawing_disagreement",
                "unmatched_record", "unmatched_records", "unresolved_links"} & set(res["synthetic"]), arrived
    # source and mapping problems: the bundle's next deliveries land, and the rebuild after them raises the rest
    assert not {"failed_quality_check", "mapping_drift", "mapping_to_approve", "source_issue", "source_quality_drop"
                } & set(res["synthetic"]), arrived
    assert arrived["mapping_drift"] == "a delivery of iow_register and the rebuild after it"
    assert done["failed_quality_check"]["steps"]["match"] == "matched the sap_work_centers source"
    assert done["unresolved_links"]["steps"]["match"] == "matched the ptw source"
    assert arrived["round_finding"] == "an operator's reading on R-10-01"  # taken on the round, outside its limits
    # a descriptor edited in PI, and Claude's cached readings of a datasheet and a scanned drawing (no model call)
    assert arrived["measurement_role_to_confirm"] == "a delivery of pi_points and the rebuild after it"
    assert arrived["curve_to_confirm"] == "1 cached reading, then the reader's review queue"
    assert arrived["drawing_line_to_confirm"] == "1 cached reading, then the reader's review queue and the rebuild after it"
    assert res["synthetic"] == [], res["synthetic"]  # every type on Okan arrives as it does in production
    assert done["criticality_not_assessed"]["steps"]["route"] == "in the data_steward queue"
    assert done["upset"]["steps"]["route"] == "started upset" and done["upset"]["steps"]["count"].endswith("(Resolved)")
    assert done["sap_notification"]["steps"]["triage"] == "on the board, to dispatch"
    assert done["source_issue"]["steps"]["field"].startswith("skipped")  # data work stays at the desk

    jobs = {r["type"]: r["job"] for r in res["types"] if "job" in r}
    if os.environ.get("FUZER_WRITE_FIXTURES"):
        JOBS.write_text(json.dumps(jobs, indent=1, sort_keys=True, default=_json) + "\n")
    kept = json.loads(JOBS.read_text())
    assert sorted(kept) == sorted(jobs), "ui/tests/fixtures/loop-field-jobs.json is stale: FUZER_WRITE_FIXTURES=1"
    assert {t: _shape(j) for t, j in kept.items()} == {t: _shape(j) for t, j in jobs.items()}, \
        "the field job changed shape: rewrite the fixture with FUZER_WRITE_FIXTURES=1 and check the 390 px test"


def test_a_type_that_cannot_complete_says_at_which_step(okan_db, monkeypatch):
    monkeypatch.setitem(loop.ARRIVE, "sap_notification", {"equipment": "fz-pump-raw-id"})  # an id the field can't read
    [r] = loop.run(okan_db.conn, only=["sap_notification"], arrive={})["types"]  # raised by the loop, so it can be bent
    assert r["failed"] == "field" and "raw ids" in r["why"] and list(r["steps"]) == list(loop.STEPS[:5])

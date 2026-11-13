"""Permit conditions (#370): the register's conditions with the recorded values beside the limit as written. On Okan the
HP separator's produced water runs over its discharge limits after OK-J03-02's water breakthrough, the healthy
conditions read within, the dew point condition with no figure shows its values and infers no limit, and the
conditions the register maps to no recorded value are unknown, never compliant."""

import csv
import json
from collections import defaultdict

import pytest

from fuzer.analysis import calcs
from fuzer.analysis import permit_conditions as pc


def test_the_produced_water_exceedance_is_found_and_nothing_is_inferred(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "permit_conditions.json").read_text())
    got = pc.conditions(conn)
    by = {c["condition"]: c for c in got["conditions"]}
    assert got["verdict"] == "exceeded" and len(by) == 8
    for verdict in ("exceeded", "within", "no_limit", "unknown"):
        assert {c for c in key[verdict] if by[c]["verdict"] == verdict} == set(key[verdict]), verdict
    mean = by["PC-OK-01"]
    assert mean["limit"]["value"] == 29 and mean["owner"] == "Environmental Advisor" and mean["applies_to"] == ["10-V-2001"]
    months = {p["period"] for p in mean["periods"] if p["status"] == "exceeded"}
    assert {"2025-07", "2025-08", "2025-09", "2025-10", "2025-11"} <= months and not any(m < key["breakthrough"][:7]
                                                                                    for m in months)
    assert all(p["figure"] > 29 for p in mean["periods"] if p["status"] == "exceeded")
    sample = by["PC-OK-02"]
    assert sample["exceeded"] and all(p["set_by"]["record"].startswith("lims:") for p in sample["periods"])
    assert mean["next_due"] == "2026-01-15"  # December's return, 15 days after the month
    dew = by["PC-OK-06"]
    assert dew["limit"] is None and dew["periods"] and all(p["status"] == "no_limit" for p in dew["periods"])
    assert "none is inferred" in dew["why"]
    for c in key["unknown"]:
        assert by[c]["periods"] == [] and "not mapped to a recorded value" in by[c]["why"]
    assert by["PC-OK-08"]["past_due"] and "doesn't say it was met" in by["PC-OK-08"]["why"]
    assert got["conditions"][0]["verdict"] == "exceeded"  # the worst first
    assert "none is inferred" in got["note"] and "not compliant" in got["note"]


def test_the_flare_consent_is_read_from_the_daily_reports(okan_db):
    conn = okan_db.conn
    got = pc.conditions(conn, ref="PC-OK-05")
    flare = got["conditions"][0]
    monthly: dict[str, float] = defaultdict(float)
    with (okan_db.data / "ec_day001.csv").open() as f:
        for r in csv.DictReader(f):
            if "2024-12-01" <= r["report_date"] <= got["as_of"] and r["flare_ksm3"]:
                monthly[r["report_date"][:7]] += float(r["flare_ksm3"])
    assert {p["period"]: p["figure"] for p in flare["periods"]} == pytest.approx(dict(monthly))
    assert flare["verdict"] == ("exceeded" if max(monthly.values()) > 3000 else "within")


def test_one_permit_and_a_missing_one(okan_db):
    conn = okan_db.conn
    env = pc.conditions(conn, ref="OK-ENV-PW-2023")
    assert {c["condition"] for c in env["conditions"]} == {"PC-OK-01", "PC-OK-02", "PC-OK-03", "PC-OK-07"}
    with pytest.raises(calcs.CalcError):
        pc.conditions(conn, ref="NO-SUCH-PERMIT")

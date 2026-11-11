"""The finding contract (P1.1): one record shape for anything detected, and nothing raised outside it."""

import ast
from datetime import datetime
from pathlib import Path

import pytest

from fuzer.core.findings import (
    AGENTIC,
    DETERMINISTIC,
    EXTERNAL,
    Finding,
    FindingError,
    asset,
    asset_ids,
    at,
    external,
    fact,
    shown,
    text,
)

pytestmark = pytest.mark.unit
SRC = Path(__file__).parents[2] / "src" / "fuzer"


def test_a_finding_is_one_record_whoever_raised_it():
    f = Finding("limits.iow", DETERMINISTIC, method="Time outside an integrity operating window",
                standard="API RP 584", start=datetime(2025, 12, 30, 4, 0), confidence=0.9, checks=["Check the PSV"],
                items=(fact("Limit", 85.0, "barg"), fact("Still going", True), at("First outside", "2025-12-30T04:00"),
                       asset("Measured on", "fz-point-0123456789ab"), fact("Note", None), fact("Empty", "")),
                data={"iow": "IOW-7", "level": "critical"})
    stored = f.evidence()
    assert stored["iow"] == "IOW-7" and stored["finding"]["v"] == 1  # the process variables beside the record
    assert [i["label"] for i in stored["finding"]["items"]] == ["Limit", "Still going", "First outside", "Measured on"]
    assert asset_ids(stored) == ["fz-point-0123456789ab"]
    read = shown(stored, "limits", ["fz-point-0123456789ab"], 25000.0, {"fz-point-0123456789ab": "30-PT-2002 suction"})
    assert read["origin_label"] == "Rule" and read["standard"] == "API RP 584" and read["start"] == "2025-12-30T04:00"
    assert read["subjects"] == ["fz-point-0123456789ab"] and read["value_usd"] == 25000.0 and read["confidence"] == 0.9
    assert [text(i) for i in read["items"]] == ["85 barg", "yes", "2025-12-30 04:00", "30-PT-2002 suction"]
    assert read["checks"] == ["Check the PSV"] and "iow" not in read
    # An asset nobody can name is left out, never shown as its id
    assert [i["label"] for i in shown(stored)["items"]] == ["Limit", "Still going", "First outside"]


def test_the_contract_refuses_what_is_not_a_finding():
    for bad in (lambda: Finding("x", "guess"), lambda: Finding("", EXTERNAL),
                lambda: Finding("x", AGENTIC, confidence=1.4), lambda: Finding("x", AGENTIC, items=(("k", "v"),)),
                lambda: Finding("x", AGENTIC, data={"finding": {}})):
        with pytest.raises(FindingError):
            bad()
    assert Finding("triage", AGENTIC, confidence=0.6).record() == {
        "v": 1, "detector": "triage", "origin": "agentic", "confidence": 0.6}


def test_what_an_outside_system_says_becomes_a_finding_in_one_place():
    f = external("scada", {"alarm_priority": "high", "drips_per_min": 12.0, "start": "2025-12-30T00:00",
                           "upset_id": 3, "detail": {"x": 1}, "via": "webhook", "on": "fz-pump-0123456789ab"})
    assert f.origin == EXTERNAL and f.data["upset_id"] == 3  # everything sent stays for the process
    assert [(i.label, i.kind) for i in f.items] == [("Alarm priority", "text"), ("Drips per min", "number"),
                                                    ("Start", "time"), ("On", "asset")]
    # Evidence stored before the contract reads the same way, with its checklist as the checks
    old = shown({"minutes_outside": 95.0, "checklist": ["Check the seal"], "field": [{"status": "done"}]}, "limits")
    assert old["detector"] == "limits" and old["origin_label"] == "Reported by" and old["checks"] == ["Check the seal"]
    assert [(i["label"], text(i)) for i in old["items"]] == [("Minutes outside", "95")]


def test_no_raiser_writes_its_own_evidence_keys():
    """Every raise under src/fuzer hands over a finding; none passes an evidence dict of its own keys."""
    calls = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            raises = name == "raise_exception" or (name == "raise_" and isinstance(fn, ast.Attribute))
            passed = name == "_exception_call" and any(
                isinstance(a, ast.Attribute) and a.attr == "raise_" for a in node.args)
            if raises or passed:
                calls.append((f"{path.relative_to(SRC)}:{node.lineno}", {k.arg for k in node.keywords}))
    calls = [(where, kw) for where, kw in calls if where.split(":")[0] != "core/exceptions.py"]  # the port passes it on
    assert len(calls) >= 10, calls  # SRE, IOW, trips, machine health, records, sources, SAP, rules, webhooks, the API
    assert [where for where, kw in calls if "evidence" in kw or "finding" not in kw] == []

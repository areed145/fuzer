"""Every bundle's schedule templates (P5.18) hold together before they reach a database: steps and links name
each other, needs name declared resources, and every asset a step names is in the bundle's own SAP hierarchy."""

import csv
from pathlib import Path

import pytest
import yaml

BUNDLES = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles"
TEMPLATES = sorted(BUNDLES.glob("*/schedules/*.yaml"))


def test_each_bundle_has_a_schedule():
    assert {p.parts[-3] for p in TEMPLATES} >= {"okan", "kestrel", "harrier"}


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_a_template_holds_together(path):
    doc = yaml.safe_load(path.read_text())
    bundle = yaml.safe_load((path.parents[1] / "bundle.yaml").read_text())
    assert f"schedules/{path.name}" in bundle["schedules"]
    codes = [a["code"] for a in doc["activities"]]
    assert len(codes) == len(set(codes)) and doc["code"] and doc["start"] and doc["finish_by"]
    resources = {r["code"] for r in doc["resources"]}
    assert all(set(r.get("counts") or []) <= resources for r in doc["resources"])
    for a in doc["activities"]:
        assert a["hours"] >= 0 and set(a.get("needs") or {}) <= resources, a["code"]
        assert all(str(link).split()[0] in codes for link in a.get("after") or []), a["code"]
    exports = path.parents[1] / "exports" / "sap_floc.csv"
    if exports.is_file():  # Okan's hierarchy is generated, and its template is checked against a built register
        # an area goes by its location code, a tag by its number: location KRGP-B1-K2001 is tag B1-K-2001
        flocs = {r["floc"].replace("-", "") for r in csv.DictReader(exports.open())}
        named = {ref for a in doc["activities"] for ref in a.get("refs") or []}
        lost = sorted(ref for ref in named if not any(f.endswith(ref.replace("-", "")) for f in flocs))
        assert not lost, lost

"""Measurement roles (P1.4a): the role proposed for every point on both bundles, against what each fake plant's
process model says the point is."""

from pathlib import Path

import pytest

from fuzer.core import roles
from fuzer.evals import roles as roles_eval

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"


@pytest.mark.parametrize("description,letters,uom,classes,want", [
    ("Train A 1st Stage Compressor Suction Pressure", "PT", "barg", ["CO"], "suction_pressure"),
    ("1st Stage Suction Scrubber A Vessel Pressure", "PT", "barg", ["VE"], "pressure"),  # a vessel's, not the machine's
    ("Compressor Shaft Vibration", "VT", "um", ["CO"], "shaft_vibration"),  # a probe in um, not a casing velocity
    ("Compressor Axial Position", "ZT", "mm", ["CO"], "axial_position"),  # not a valve
    ("Inlet Filter Coalescer Differential Pressure", "PDT", "bar", ["VE"], "differential_pressure"),
    ("Well KR 12-03H Tubing Head Pressure", None, "barg", ["WELL"], "wellhead_pressure"),
    ("Dry Gas Water Content", "AT", "mg/Sm3", [], "composition"),
    ("Engine Cylinder 6 Exhaust Temperature", "TT", "degC", ["CO"], "exhaust_temperature"),
    ("Compressor Cylinder 4 Discharge Temperature", "TT", "degC", ["CO"], "cylinder_discharge_temperature"),
])
def test_a_role_is_proposed_from_words_letters_units_and_the_equipment(description, letters, uom, classes, want):
    r = roles.propose(description, letters, uom, classes)
    assert r["role"] == want, r
    assert r["confidence"] >= roles.CONFIDENT


def test_a_guess_from_the_equipment_alone_or_disagreeing_evidence_goes_to_a_steward():
    guessed = roles.propose("Amine Reboiler Temperature", "TT", "degC", ["HE"])
    assert guessed["role"] == "outlet_temperature" and guessed["confidence"] < roles.CONFIDENT
    assert any("usually" in b for b in guessed["basis"])
    torn = roles.propose("Separator Pressure", "TT", "degC", ["VE"])  # letters and units say one thing, words another
    assert torn["confidence"] < roles.CONFIDENT
    assert roles.propose("Spare", None, None)["role"] is None


def test_instrument_letters_come_from_the_tag_grammar_or_an_isa_tag():
    assert roles.letters_in("OK_GGCP:20-PDT-2100A.PV") == "PDT"
    assert roles.letters_in("KRGP:B1.ST2004.PV") == "ST"
    assert roles.letters_in("OK_J01_01.WHP") is None


def test_every_role_the_answer_key_and_the_detectors_name_is_on_the_list():
    from fuzer.core import detectors

    assert set(roles_eval.VARIABLE_ROLE.values()) <= set(roles.ROLES)
    assert all(set(d.roles) <= set(roles.ROLES) for d in detectors.registry().values())
    with pytest.raises(detectors.DetectorError, match="isn't a measurement role"):
        detectors.register("bad_roles", title="t", kind=detectors.RULE, cadence="daily", roles=("suction psi",))


def _check(res):
    assert res["points"] > 0
    assert res["accuracy"] >= 0.9, res["misses"][:10]
    assert res["confident_misses"] == 0, [m for m in res["misses"] if m["confidence"] >= roles.CONFIDENT]


def test_at_least_90_percent_of_okan_roles_are_right_and_the_rest_go_to_a_steward(okan_db):
    _check(roles_eval.score(okan_db.conn, okan_db.data))


def test_at_least_90_percent_of_kestrel_roles_are_right_and_the_rest_go_to_a_steward(kestrel_db):
    _check(roles_eval.score(kestrel_db, KESTREL / "exports"))

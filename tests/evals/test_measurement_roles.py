"""Measurement roles (P1.4a): the role proposed for every point on both bundles, against what each fake plant's
process model says the point is."""

import importlib
import inspect
import re
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


# What compressor monitoring reads about a point (P1.4c): its role and the ISO 14224 class of what it is on, never
# its description, its units or the letters in a tag name. The run signal and vibration are checked on Okan with the
# descriptions blanked and the tags renamed (tests/analysis/test_trips.py).
_TAG_WORDS = re.compile(r"description|\buom\b|ilike|mm/s|\"rpm\"|\"kW\"|-[A-Z]{1,3}-")


@pytest.mark.parametrize("fn", ["analysis.trips:_family", "analysis.trips:_points", "analysis.rotating:_ambient_point"])
def test_compressor_monitoring_reads_roles_not_descriptions_or_tag_patterns(fn):
    module, name = fn.split(":")
    code = inspect.getsource(getattr(importlib.import_module(f"fuzer.{module}"), name))
    body = code.split('"""', 2)[-1] if code.count('"""') >= 2 else code  # its docstring may say what it doesn't read
    assert not _TAG_WORDS.findall(body), (fn, _TAG_WORDS.findall(body))


@pytest.mark.parametrize("role,classes,direction,want", [
    ("vibration", ["CO"], 1, "vibration"), ("bearing_temperature", ["CO"], 1, "bearing"),
    ("anti_surge_valve_position", ["CO"], 1, "surge"), ("speed", ["CO"], -1, "driver"),
    ("pressure", ["GT"], 1, "driver"), ("level", ["VE"], 1, "liquid_carryover"), ("level", ["VE"], -1, "process"),
    ("cylinder_discharge_temperature", ["CO"], 1, "discharge_temperature"),
    ("discharge_temperature", ["CO"], 1, "discharge_temperature"), ("discharge_temperature", ["VE"], 1, "process"),
    ("outlet_temperature", ["HE"], 1, "cooling"), ("pressure", ["VE"], -1, "suction_pressure"),
    ("suction_pressure", ["CO"], -1, "suction_pressure"), ("interstage_pressure", ["CO"], 1, "discharge_pressure"),
    ("axial_position", ["CO"], 1, "process"), (None, [], 1, "process"),
])
def test_a_trip_cause_family_comes_from_the_role_and_the_class(role, classes, direction, want):
    from fuzer.analysis import trips

    assert trips._family(role, classes, direction) == want


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


def test_uncertain_roles_are_steward_work_and_a_stewards_role_survives_rebuilds(kestrel_db):
    """P1.4b: each uncertain proposal is a Review item; confirming or choosing a role pins it for every rebuild."""
    from fastapi import HTTPException

    from fuzer.api.graph import Decision, review_decide
    from fuzer.core.pack import current_pack
    from fuzer.graph import roles as graph_roles

    conn = kestrel_db
    asked = conn.execute("select * from review_item where kind = 'role' and status = 'open' order by subject").fetchall()
    unsure = conn.execute("select count(*) as n from measurement_role where confidence < %s",
                          (roles.CONFIDENT,)).fetchone()["n"]
    assert asked and len(asked) == unsure
    first, second = asked[0], asked[1]
    p = first["payload"]
    assert p["point"] and p["proposed"] and p["basis"] and p["asset_label"]
    assert p["choices"][0]["key"] in {r.key for r in roles.ROLES.values()
                                      if r.quantity == roles.ROLES[p["proposed"]].quantity}  # its kind first
    case = conn.execute("select title from exception_record where type = 'measurement_role_to_confirm' "
                        "and key = %s and state = 'open'", (f"role:{first['subject']}",)).fetchone()
    assert case and case["title"].startswith(f"Is {p['point']} on ")  # a case in the data steward's queue

    try:
        with pytest.raises(HTTPException):
            review_decide(first["id"], Decision(decision="reject", by="sam"), conn=conn)
        with pytest.raises(HTTPException):
            review_decide(first["id"], Decision(decision="choose", value="suction psi", by="sam"), conn=conn)
        review_decide(first["id"], Decision(decision="choose", value="temperature", by="sam"), conn=conn)
        review_decide(second["id"], Decision(decision="accept", by="sam"), conn=conn)
        live = conn.execute("select role, confidence from measurement_role where point_id = %s",
                            (first["subject"],)).fetchone()
        assert (live["role"], live["confidence"]) == ("temperature", 1.0)  # live before any rebuild

        stats = graph_roles.build(conn, current_pack(conn))
        conn.commit()
        assert stats["roles_confirmed"] == 2 and stats["roles_unsure"] == unsure - 2
        after = {r["point_id"]: r for r in conn.execute("select * from measurement_role where point_id = any(%s)",
                                                         ([first["subject"], second["subject"]],))}
        assert after[first["subject"]]["role"] == "temperature"
        assert after[second["subject"]]["role"] == second["payload"]["proposed"]
        assert after[first["subject"]]["basis"] == ["confirmed by sam"]
        still = {r["subject"] for r in conn.execute("select subject from review_item where kind = 'role' and status = 'open'")}
        assert not still & {first["subject"], second["subject"]}
    finally:  # the scores on this bundle are of the proposer, not of a steward's answers
        conn.execute("delete from measurement_role_pin")
        graph_roles.build(conn, current_pack(conn))
        conn.commit()

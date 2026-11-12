"""The method registry (P1.3): every calc is a registered method with a standard, inputs, validity and a test vector."""

import importlib
from pathlib import Path

import pytest

from fuzer.analysis import calcs
from fuzer.core import methods

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]
REGISTRY = methods.registry()


def test_every_check_is_a_registered_method_and_nothing_else_is():
    assert set(calcs.CHECKS) == set(REGISTRY)


@pytest.mark.parametrize("m", REGISTRY.values(), ids=list(REGISTRY))
def test_a_method_names_its_standard_inputs_validity_and_vectors(m):
    assert m.title and (m.standard or m.practice) and m.valid and m.needs
    assert not (m.clause and not m.standard)  # a clause belongs to a standard
    assert m.inputs and all(i["name"] and i["unit"] for i in m.inputs)
    assert m.vectors, "at least one test vector or worked example"
    for t in m.thresholds.values():
        assert isinstance(t["value"], (int, float)) and t["means"]
        if "code" in t:  # the calculation reads the same number
            mod, name = t["code"].split(":")
            assert getattr(importlib.import_module(mod), name) == t["value"], t["code"]


def _close(got, want) -> bool:
    if isinstance(want, dict):
        return all(_close(got[k], v) for k, v in want.items())
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return got == pytest.approx(want, rel=1e-3, abs=1e-4)
    return got == want


VECTORS = [(m.id, v) for m in REGISTRY.values() for v in m.vectors]


@pytest.mark.parametrize(("method", "v"), VECTORS, ids=[f"{m}-{i}" for i, (m, _) in enumerate(VECTORS)])
def test_a_vector_gives_its_known_answer(method, v):
    if "worked" in v:  # a test that runs the method on the fake plant's injected story
        path, name = v["worked"].split("::")
        assert f"def {name}(" in (ROOT / path).read_text(), v["worked"]
        return
    mod, fn = v["kernel"].split(":")
    args = [tuple(a) if fn == "zone_of" and isinstance(a, list) else a for a in v["args"]]
    assert v["says"] and _close(getattr(importlib.import_module(mod), fn)(*args), v["expect"]), method


def test_a_result_names_its_method_as_a_person_reads_it():
    assert methods.label("corrosion_circuit").startswith("API 570 7.1, corrosion circuit: long and short term rate")
    assert methods.label("gas_lift") == "Lift gas against the economic optimum of the well's lift curve"
    assert methods.describe("vibration") == {
        "id": "vibration", "title": "Overall vibration against the evaluation zones", "label": methods.label("vibration"),
        "standard": "ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors"}
    with pytest.raises(methods.MethodError):
        methods.get("nope")


def test_a_method_that_cannot_answer_refuses_by_name():
    for reason, name in (("fewer than four well tests on that well", "too_little_data"),
                         ("the stage hardly ran in that window", "too_little_data"),
                         ("no wall thickness readings on that item", "missing_input"),
                         ("the OEM curve gives no best efficiency point", "missing_input"),
                         ("not a centrifugal compressor stage tag (NN-K-N?00X)", "not_applicable"),
                         ("unknown entity", "not_applicable"),
                         ("every time between failures is the same: nothing to fit", "insufficient_inputs")):
        assert methods.refusal_name(reason) == name, reason
    out = methods.refusal("wall_loss", "no wall thickness readings on that item")
    assert out["refused"] == "missing_input" and out["method"]["id"] == "wall_loss" and out["refused"] in methods.REFUSALS
    e = calcs.CalcError("no curve")  # outside the registry it is still an error with its reason
    assert e.refusal() == {"error": "no curve"}

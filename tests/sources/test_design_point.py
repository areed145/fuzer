"""Okan's design point against an equation of state (#858, step 6): fakeplant/calibration.py."""

import csv
import json

import pytest

from fakeplant import calibration, process

# Found by these checks and not fixed yet: Fuzer's Schultz efficiency on an ideal k reads the 1st stage's heavier gas
# about 0.04 high. A fix should take its entry out of here.
OPEN = {"fuzer_efficiency 20-K-2100A"}


@pytest.fixture(scope="module")
def report(okan_data):
    spec = json.loads((okan_data / "process_model.json").read_text())
    rd = [float(r["result"]) for r in csv.DictReader((okan_data / "lims_results.csv").open())
          if r["analyte"] == "relative_density"]
    return calibration.report(spec, sum(rd) / len(rd), "20-V-2001", {k: v[1] for k, v in process.STAGE_DESIGN.items()})


def _ok(report, name):
    return {c["unit"]: c["ok"] for c in report["checks"] if c["check"] == name}


def test_the_gas_analysis_is_the_inlet_separators_gas_at_its_dew_point(report):
    (gas,) = [c for c in report["checks"] if c["check"] == "inlet_gas_relative_density"]
    assert gas["ok"], gas
    assert calibration.dew_point(list(report["gas"]["composition"].values()), 6.5) == pytest.approx(35.5, abs=0.2)


def test_compressor_discharge_temperatures_are_a_centrifugal_stages_efficiency_on_its_gas(report):
    eff = [c for c in report["checks"] if c["check"] == "stage_efficiency"]
    assert len(eff) == 3 and all(c["ok"] and 0.75 <= c["physics"] <= 0.79 for c in eff), eff
    assert all(_ok(report, f"fuzer_z_{end}").values() for end in ("suction", "discharge"))


def test_compressor_maps_and_gas_turbines_match_the_head_the_stages_do(report):
    heads, power = _ok(report, "stage_head"), _ok(report, "train_power_closure")
    assert len(heads) >= 3 and all(heads.values()) and len(power) == 3 and all(power.values())


def test_gas_producers_flow_into_their_header_above_their_hydrate_temperature(report):
    flows, hydrate = _ok(report, "gas_well_flows"), _ok(report, "gas_well_hydrate_margin")
    assert len(flows) == 3 and all(flows.values()) and all(hydrate.values())


def test_only_the_known_open_findings_fail(report):
    open_ = {f for f in report["failed"] if f.split()[0] in OPEN or f in OPEN}
    assert set(report["failed"]) == open_, report["failed"]

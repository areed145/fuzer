"""CO2 corrosion screening (#368): Okan's production lines carry the same water at much the same temperature, so the
model predicts them alike; J03's wet flowline measures far more of its prediction than its peers and is out of
step. The prediction is a stated screening model, never a verdict."""

import json

from fuzer.analysis import co2_corrosion


def test_the_wet_flowline_corrodes_out_of_step_with_its_prediction(okan_db):
    conn = okan_db.conn
    out = co2_corrosion.screen(conn)
    assert out["summary"]["lines"] >= 10 and out["summary"]["compared"] >= 10
    top = out["lines"][0]
    key = json.loads((okan_db.data / "answer_keys" / "co2_corrosion.json").read_text())
    assert top["flag"] == "out_of_step" and top["line"] == key["out_of_step"]
    assert out["summary"]["flagged"] == [top["line"]]
    # its water is its peers' water: the line, not the chemistry, is what differs
    # the model predicts the uninhibited rate: every line measures well under it, the wet one included
    assert all(x["measured_over_predicted"] < 1 for x in out["lines"] if x.get("measured_over_predicted") is not None)
    assert top["measured_over_predicted"] > co2_corrosion.OUTLIER_RATIO * out["typical_ratio"]
    assert abs(top["water"]["chloride"] - 17500) < 1500 and "field's gas samples" in top["gas_from"]
    assert top["pco2_ph2s"] > co2_corrosion.SOUR_RATIO
    assert "not an integrity verdict" in out["note"]
    from fuzer.plots import maintenance
    fig = maintenance.co2_corrosion(conn)
    assert fig.summary["verdict"] == "flagged" and fig.layout["yaxis"]["type"] == "log"
    conn.rollback()


def test_no_water_chemistry_means_no_prediction(okan_db):
    out = co2_corrosion.screen(okan_db.conn, until="2023-06-01")
    assert out["verdict"] == "no_water_chemistry" and out["lines"] == []


def test_de_waard_milliams_matches_the_hand_calculation():
    m = co2_corrosion.predicted_rate(55.0, 71.0, 2.1)
    # T 328.15 K; pCO2 1.491 bar; fugacity 10^(71 x (0.0031 - 1.4/328.15)) = 0.826; below the scaling temperature
    assert round(m["pco2_bar"], 3) == 1.491 and round(m["fugacity_coefficient"], 3) == 0.826
    assert m["scale_factor"] == 1.0 and round(m["rate_mm_per_year"], 2) == 4.46

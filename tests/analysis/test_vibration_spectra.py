"""Vibration spectra (#367): Okan's monthly pump spectra. 10-P-1001B's NDE bearing shows the outer race defect at its
ball pass frequency and harmonics from August 2025, growing while the overall level barely moves; no other point
shows an indication."""

import json

from fuzer.analysis import vibration_spectra as vs


def test_the_planted_outer_race_defect_shows_at_its_frequency(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "vibration_spectra.json").read_text())
    out = vs.spectra(conn)
    assert out["verdict"] == "indications"
    assert out["summary"]["with_indications"] == [f"{key['tag']} {key['point']}"]
    p = out["points"][0]
    (i,) = p["indications"]
    assert i["fault"] == key["defect"] and i["order"] == key["bpfo_order"]
    assert abs(i["hz"] - key["bpfo_order"] * p["rpm"] / 60) < 0.5 and i["harmonics"] == [1, 2, 3]
    assert i["since"].startswith(key["first"])
    amps = [t["amplitude"] for t in i["trend"]]
    assert amps[-1] > 3 * amps[0]  # growing
    assert p["overall"] < 2 * p["overall_first"]  # the overall level hardly says so
    assert "not a diagnosis" in out["note"]
    one = vs.spectra(conn, tag="10-P-1001A")
    assert one["verdict"] == "no_indications" and {q["tag"] for q in one["points"]} == {"10-P-1001A"}
    from fuzer.plots import maintenance
    fig = maintenance.vibration_spectrum(conn, key["tag"], key["point"])
    assert any(sh.get("name") == "bpfo" for sh in fig.summary["markers"])
    conn.rollback()


def test_bearing_frequencies_match_the_textbook_formulas():
    f = vs.bearing_frequencies(3000, 8, 23.8, 100.0, 0)
    # fr 50 Hz, Bd/Pd 0.238: BPFO = 4 x 50 x 0.762, BPFI = 4 x 50 x 1.238, FTF = 25 x 0.762
    assert round(f["bpfo"], 1) == 152.4 and round(f["bpfi"], 1) == 247.6 and round(f["ftf"], 2) == 19.05
    assert round(f["bsf"], 1) == round(100 / (2 * 23.8) * 50 * (1 - 0.238 ** 2), 1)
    assert vs.parse_peaks("49.6:1.0;bad;151.2:0.4") == [(49.6, 1.0), (151.2, 0.4)]

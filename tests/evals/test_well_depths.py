"""Depth positions (#61): a well's directional survey becomes its trajectory by minimum curvature, and its completion
components sit at their measured depths with the true vertical depth the survey gives them, on Kestrel."""

import math

import pytest

from fuzer.graph import positions


def _id(conn, key: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (key,)).fetchone()["fuzer_id"]


def test_minimum_curvature_matches_a_hand_worked_build_section():
    # vertical to 1000 m, then a constant build of 3 degrees per 30 m to horizontal: a quarter circle of radius
    # 30 / (3 deg in rad) = 572.96 m, so 900 m of hole lands 572.96 m deeper and 572.96 m out
    st = [(0.0, 0.0, 90.0), (1000.0, 0.0, 90.0)] + [(1000.0 + 30 * k, 3.0 * k, 90.0) for k in range(1, 31)]
    t = positions.min_curvature(st)
    r = 30 / math.radians(3)
    assert abs(t[-1]["tvd_m"] - (1000 + r)) < 0.1 and abs(t[-1]["east_m"] - r) < 0.1 and abs(t[-1]["north_m"]) < 1e-6
    assert t[-1]["dls_deg_30m"] == 3.0
    assert positions.tvd_at(t, 500) == 500.0 and positions.tvd_at(t, 5000) is None


def test_a_well_page_shows_its_trajectory_and_completion_by_depth(kestrel_db):
    conn = kestrel_db
    w = positions.well(conn, _id(conn, "KR 12-03H"))
    s = w["summary"]
    assert s["td_md_m"] == 4600 and 89 < s["max_inclination_deg"] < 91
    assert 2400 < s["td_tvd_m"] < 2550  # a horizontal well lands a few hundred metres below its kick-off
    assert s["displacement_m"] > 2000 and w["trajectory"][-1]["north_m"] > 2000  # drilled north
    # its pad twin, drilled south, came from the contractor as WITSML in feet: the same trajectory in metres
    south = positions.well(conn, _id(conn, "KR 12-04H"))
    assert south["trajectory"][-1]["md_m"] == 4600 and south["trajectory"][-1]["north_m"] < -2000
    names = [c["description"] for c in w["components"]]
    assert names[:2] == ["surface casing", "intermediate casing"] and "production packer" in names
    stages = [c for c in w["components"] if str(c["description"]).startswith("frac stage")]
    assert len(stages) == 5 and all(c["top_tvd_m"] is not None and abs(c["top_tvd_m"] - s["td_tvd_m"]) < 40
                                    for c in stages)  # stages are in the lateral, near landing depth
    surface = next(c for c in w["components"] if c["description"] == "surface casing")
    assert surface["base_tvd_m"] == surface["base_md_m"] == 610  # vertical above the kick-off: MD and TVD agree


def test_the_well_figure_draws_the_section_and_lists_the_completion(kestrel_db):
    from fuzer.core import pack as packs
    from fuzer.plots import analytics

    conn = kestrel_db
    pack = packs.current_pack(conn)
    well = _id(conn, "KR 12-03H")
    fig = analytics.live(conn, pack, "well-trajectory", well, well=well)
    s = fig["summary"]
    assert s["headline"].startswith("Drilled to 4,600 m along the hole")
    assert [t["name"] for t in fig["spec"]["data"]] == ["Hole (survey)", "Completion components"]
    [table] = s["tables"]
    assert table["rows"][0][:3] == ["surface casing", "0", "610"] and len(table["rows"]) == 12
    assert s["warnings"] == []


def test_a_witsml_trajectory_reads_in_metres_and_degrees_whatever_its_units(tmp_path):
    from fuzer.sources import witsml

    (tmp_path / "t.xml").write_text("""<trajectorys xmlns="http://www.witsml.org/schemas/1series" version="1.4.1.1">
      <trajectory uidWell="W1" uid="T1"><nameWell>X-1</nameWell>
        <trajectoryStation uid="a"><md uom="ft">1000</md><incl uom="rad">0.5235987756</incl><azi uom="dega">45</azi></trajectoryStation>
        <trajectoryStation uid="b"><incl uom="dega">1</incl></trajectoryStation>
      </trajectory></trajectorys>""")
    [(well, key, p)] = list(witsml.read(tmp_path / "t.xml"))
    assert (well, key, p["md_m"], p["inclination_deg"], p["azimuth_deg"]) == ("X-1", "T1-a", 304.8, 30.0, 45.0)
    (tmp_path / "bad.xml").write_text('<trajectorys><trajectory><nameWell>X</nameWell><trajectoryStation>'
                                      '<md uom="furlong">1</md></trajectoryStation></trajectory></trajectorys>')
    with pytest.raises(ValueError, match="furlong"):
        list(witsml.read(tmp_path / "bad.xml"))

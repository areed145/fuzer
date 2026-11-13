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
    assert [t["name"] for t in fig["spec"]["data"]][:2] == ["Hole (survey)", "Completion components"]
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


def test_a_wells_depth_log_sits_beside_its_trajectory_by_true_vertical_depth(kestrel_db):
    conn = kestrel_db
    w = positions.well(conn, _id(conn, "KR 12-03H"))
    gr = next(lg for lg in w["logs"] if lg["curve"] == "GR")
    assert gr["unit"] == "GAPI" and gr["md_m"][0] == 600.0 and gr["md_m"][-1] == 4600.0 and len(gr["values"]) == 2001
    rt = next(lg for lg in w["logs"] if lg["curve"] == "RT")
    assert rt["md_m"][0] == 606.0  # the resistivity tool's first readings were null and are left out
    # in the lateral, the hole stays in the target sand: low gamma ray at landing depth
    land = w["summary"]["td_tvd_m"]
    lateral = [v for v, t in zip(gr["values"], gr["tvd_m"]) if t is not None and abs(t - land) < 3]
    assert lateral and sum(lateral) / len(lateral) < 50
    assert positions.well(conn, _id(conn, "KR 12-04H"))["logs"] == []  # no log loaded for its twin


def test_the_well_figure_draws_the_gamma_ray_track_beside_the_section(kestrel_db):
    from fuzer.core import pack as packs
    from fuzer.plots import analytics

    conn = kestrel_db
    well = _id(conn, "KR 12-03H")
    fig = analytics.live(conn, packs.current_pack(conn), "well-trajectory", well, well=well)
    names = [t["name"] for t in fig["spec"]["data"]]
    assert names[:2] == ["Hole (survey)", "Completion components"] and "GR (GAPI)" in names
    lay = fig["spec"]["layout"]
    assert lay["xaxis"]["domain"] == [0, 0.56] and lay["xaxis3"]["type"] == "log"  # resistivity on a log scale
    assert any("KR12-03H_lwd.las" in n for n in fig["summary"]["notes"])


def test_a_witsml_2_trajectory_reads_as_the_same_stations_as_its_1_4_1_twin(tmp_path):
    from fuzer.resources import BUNDLES
    from fuzer.sources import witsml

    old = [p for w, _, p in witsml.read(BUNDLES / "kestrel" / "exports" / "witsml_trajectories.xml")
           if w == "KR 12-04H"]
    v2 = "http://www.energistics.org/energyml/data/witsmlv2"
    stations = "".join(
        f'<TrajectoryStation uid="s{i}"><Md uom="m" datum="KB">{p["md_m"]}</Md>'
        f'<TypeTrajStation>{p["tool"] or "MWD"}</TypeTrajStation><Incl uom="dega">{p["inclination_deg"]}</Incl>'
        f'<Azi uom="dega">{p["azimuth_deg"]}</Azi></TrajectoryStation>' for i, p in enumerate(old))
    (tmp_path / "t2.xml").write_text(
        f'<Trajectory xmlns="{v2}" xmlns:eml="http://www.energistics.org/energyml/data/commonv2" uuid="u-1" '
        f'schemaVersion="2.0"><eml:Citation><eml:Title>survey</eml:Title></eml:Citation>'
        f'<Wellbore><eml:Title>KR 12-04H</eml:Title></Wellbore>{stations}</Trajectory>')
    new = list(witsml.read(tmp_path / "t2.xml"))
    assert {w for w, _, _ in new} == {"KR 12-04H"} and new[0][2]["format"] == "witsml 2.0"
    assert [(p["md_m"], p["inclination_deg"], p["azimuth_deg"]) for _, _, p in new] == \
        [(p["md_m"], p["inclination_deg"], p["azimuth_deg"]) for p in old]


def test_the_wells_agent_reads_a_well_by_depth_with_the_records_it_rests_on(kestrel_db):
    conn = kestrel_db
    b = positions.brief(conn, _id(conn, "KR 12-03H"))
    assert b["found"] and b["summary"]["td_md_m"] == 4600
    assert b["sources"]["log_files"] == ["well_logs/KR12-03H_lwd.las"] and list(b["sources"]["survey_stations"]) == ["csv"]
    stages = [c for c in b["components"] if c["component"].startswith("frac stage")]
    assert len(stages) == 5 and all(c["log_median"]["GR"] < 60 for c in stages)  # the stages are in the sand
    casing = next(c for c in b["components"] if c["component"] == "surface casing")
    assert casing["log_median"]["GR"] > 90  # the log starts at 600 m, in the shale at the casing shoe
    assert "samples" in b["logs"][0] and "values" not in b["logs"][0]  # the agent gets no arrays
    south = positions.brief(conn, _id(conn, "KR 12-04H"))
    assert south["found"] and list(south["sources"]["survey_stations"]) == ["witsml 1.4.1"]
    assert "log_median" not in south["components"][0]

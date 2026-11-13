"""Positions along a line (#61): Harrier's in-line inspection features land on the segment whose station range holds
them, read from PODS-style route segments, and go on the map along the line's route by station; a feature on a line
the register doesn't know is not dropped."""

from fuzer.core import pack as packs
from fuzer.graph import positions, queries


def _id(conn, key: str) -> str:
    return conn.execute("select fuzer_id from entity_key where key = %s", (key,)).fetchone()["fuzer_id"]


def test_ili_features_link_to_the_right_segment_and_station(harrier_db):
    conn = harrier_db
    line = _id(conn, "HR-PL-001")
    out = positions.along(conn, line)
    assert [s["label"] for s in out["segments"]] == [f"HR-PL-001-S0{i}" for i in range(1, 6)]
    where = {f["label"]: (f["segment"], f["station_m"]) for f in out["features"] if f["run_id"] == "RUN-2024-MFL"}
    seg = {s["id"]: s["label"] for s in out["segments"]}
    expect = {"ILI-24-0007": "S01", "ILI-24-0031": "S02", "ILI-24-0032": "S02", "ILI-24-0058": "S03",
              "ILI-24-0071": "S03", "ILI-24-0072": "S04", "ILI-24-0094": "S04", "ILI-24-0118": "S05"}
    assert {k: seg[v[0]][-3:] for k, v in where.items()} == expect
    assert where["ILI-24-0031"][1] == 38912.3
    # the same links are in the graph, with the station on the link
    edge = conn.execute("select e.props from edge e where e.src = %s and e.rel = 'found_on' and e.dst = %s",
                        (_id(conn, "ILI-24-0031"), _id(conn, "HR-PL-001-S02"))).fetchone()
    assert edge["props"]["station_m"] == 38912.3
    # a segment shows only its own features
    assert [f["label"] for f in positions.along(conn, _id(conn, "HR-PL-001-S02"))["features"]] == [
        "ILI-24-0031", "ILI-26-0029", "ILI-24-0032", "ILI-26-0030"]  # both runs (#366), in station order


def test_features_and_segments_are_placed_along_the_route_by_station(harrier_db):
    conn = harrier_db
    route = conn.execute("select geometry from geo_index where fuzer_id = %s", (_id(conn, "HR-PL-001"),)).fetchone()
    st = positions.stationing(route["geometry"], 210_000)
    assert st[0][0] == 0 and round(st[-1][0]) == 210_000
    first = route["geometry"]["coordinates"][0]
    near = conn.execute("select geometry from geo_index where fuzer_id = %s", (_id(conn, "ILI-24-0007"),)).fetchone()
    lon, lat = near["geometry"]["coordinates"]
    assert abs(lon - first[0]) < 0.02 and abs(lat - first[1]) < 0.01  # 1.2 km from the origin
    seg = conn.execute("select geometry from geo_index where fuzer_id = %s", (_id(conn, "HR-PL-001-S05"),)).fetchone()
    assert seg["geometry"]["type"] == "LineString" and seg["geometry"]["coordinates"][-1] == route["geometry"][
        "coordinates"][-1]


def test_a_feature_on_a_line_the_register_doesnt_know_is_kept(harrier_db):
    conn = harrier_db
    pack = packs.current_pack(conn)
    stray = queries.resolve_ref(conn, pack, "ILI-24-0119")
    assert stray, "the feature is in the register"
    assert not conn.execute("select 1 from edge where src = %s and rel = 'found_on'", (stray,)).fetchone()
    # the source's link coverage counts it, where the data steward reads it
    stats = conn.execute("select stats from source_match_stat where source = 'inspection' order by id desc limit 1"
                         ).fetchone()["stats"]
    assert stats["coverage"]["by_rel"]["found_on"] == [17, 18]  # two runs on the mainline (#366), one stray

"""P5.16a: a schematic scene of an area from the register alone. Shapes come from ISO 14224 classes, positions from the
links (flow order along x, parallel trains side by side), and nothing in the area is dropped without being counted."""

import pytest

from fuzer.core import shapes
from fuzer.graph import scene, structure


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


@pytest.fixture(scope="module")
def compression(okan_db):
    """The scene asked for from a compressor: its area, with the compressor as the focus."""
    conn = okan_db.conn
    k = _fid(conn, "20-K-2200B")
    return conn, k, scene.scene(conn, k)


def test_an_item_opens_its_area_with_itself_in_focus(compression):
    _conn, k, s = compression
    assert s["root"]["kind"] == "area" and s["focus"] == k
    assert [p["label"] for p in s["plates"] if p["area"]] == [s["root"]["label"]]
    assert s["counts"]["items"] == len(s["items"]) and s["counts"]["links"] == len(s["links"])


def test_shapes_come_from_iso_14224_classes(compression):
    _conn, _k, s = compression
    by = {i["label"]: i for i in s["items"]}
    assert by["20-K-2200B"]["shape"] == "compressor" and by["20-E-2250B"]["shape"] == "exchanger"
    assert by["20-V-2201B"]["shape"] == "vessel" and by["20-V-2001"]["shape"] == "separator"  # VE.SE before VE
    assert by["20-GT-2000B"]["shape"] == "driver"
    assert all(i["shape"] in s["shapes"] for i in s["items"])


def test_each_train_is_a_lane_in_flow_order_and_trains_sit_side_by_side(compression):
    conn, _k, s = compression
    at = {i["id"]: i for i in s["items"]}
    plant = structure.load(conn)
    lanes = {}
    for letter in "ABC":
        t = structure.train(conn, _fid(conn, f"20-K-2200{letter}"), plant)
        piped = [m for m in t["members"] if m not in plant.drives.values()]
        xs = [at[m]["x"] for m in piped]
        assert xs == sorted(xs) and len(set(xs)) == len(xs), "flow order along x"
        assert len({at[m]["z"] for m in piped}) == 1, "one lane"
        lanes[letter] = at[piped[0]]["z"]
        driver = at[plant.drives[_fid(conn, f"20-K-2200{letter}")]]
        assert driver["placed"] == "beside what it drives" and 0 < driver["z"] - lanes[letter] <= scene.CELL_Z
    assert len(set(lanes.values())) == 3
    header = at[_fid(conn, "20-V-2001")]
    assert header["x"] < min(at[_fid(conn, f"20-V-2101{c}")]["x"] for c in "ABC")  # the shared header comes first
    assert min(lanes.values()) <= header["z"] <= max(lanes.values()) + scene.CELL_Z


def test_no_two_items_share_a_place(compression):
    _conn, _k, s = compression
    places = [(i["x"], i["z"]) for i in s["items"]]
    assert len(places) == len(set(places))


def test_links_run_from_their_source_to_their_target_and_a_shaft_is_no_pipe(compression):
    _conn, _k, s = compression
    at = {i["id"]: i for i in s["items"]}
    assert s["links"] and "drives" not in {link["rel"] for link in s["links"]} | set(s["rels"])
    for link in s["links"]:
        a, b = at[link["src"]], at[link["dst"]]
        assert link["path"][0][::2] == [a["x"], a["z"]] and link["path"][-1][::2] == [b["x"], b["z"]]
        for p, q in zip(link["path"], link["path"][1:], strict=False):
            assert sum(1 for u, v in zip(p, q, strict=True) if u != v) == 1, "runs are square to the axes"
    gas = [link for link in s["links"] if link["rel"] == "lift_gas_to"]
    assert gas and all(link["product"] == "lift_gas" for link in gas)


def test_what_it_connects_to_elsewhere_is_drawn_and_says_where(compression):
    _conn, _k, s = compression
    away = [i for i in s["items"] if i["plate"] == scene.ELSEWHERE]
    assert "10-V-2001" in {i["label"] for i in away} and all(i["area"] for i in away)
    assert {p["label"] for p in s["plates"] if not p["area"]} >= {"Connected elsewhere"}


def test_everything_in_the_area_is_drawn_or_counted(compression):
    conn, _k, s = compression
    members = conn.execute(
        "select count(distinct e.src) as n from edge e join entity en on en.fuzer_id = e.src "
        "where e.dst = %s and e.rel in ('part_of', 'located_at') and e.valid_to is null and en.merged_into is null "
        "and en.kind <> 'area'", (s["root"]["id"],)).fetchone()["n"]
    drawn = sum(1 for i in s["items"] if i["plate"] == s["root"]["id"])
    assert drawn + s["counts"]["not_drawn"] == members and s["counts"]["not_drawn"] > 0
    assert s["counts"]["by_layout"] == len(s["items"]) and s["counts"]["by_geometry"] == 0


def test_the_same_plant_draws_the_same_way(compression):
    conn, k, s = compression
    assert scene.scene(conn, k) == s


def test_a_facility_draws_every_area_and_a_jacket_stands_on_a_frame(okan_db):
    conn = okan_db.conn
    s = scene.scene(conn)
    assert s["root"]["kind"] == "facility"
    areas = [p for p in s["plates"] if p["area"]]
    assert len(areas) == conn.execute("select count(*) as n from entity where kind = 'area'").fetchone()["n"]
    assert {p["frame"] for p in areas if "jacket" in (p["type"] or "")} == {"lattice"}
    for i, p in enumerate(s["plates"]):  # plates never overlap
        for q in s["plates"][i + 1:]:
            assert p["x"] + p["w"] <= q["x"] or q["x"] + q["w"] <= p["x"] or p["z"] + p["d"] <= q["z"] or q["z"] + q["d"] <= p["z"]
    wells = [i for i in s["items"] if i["kind"] == "well"]
    assert wells and {i["shape"] for i in wells} == {"well"}
    assert {"id", "kind", "label", "description", "parent"} <= set(s["places"][0])
    assert all(p["description"] for p in areas), "each area plate carries its description, for its common name"


def test_something_in_no_area_says_so_instead_of_drawing_nothing(okan_db):
    conn = okan_db.conn
    loose = conn.execute("select fuzer_id from entity where kind = 'document' limit 1").fetchone()["fuzer_id"]
    s = scene.scene(conn, loose)
    assert s["root"] is None and s["basis"] == "not in an area" and s["items"] == []


class _Pack:
    def __init__(self, raw):
        self.scene_shapes = shapes.checked(raw)


def test_a_bundle_changes_a_shape_without_code(compression):
    conn, k, s = compression
    mine = {"shapes": {"skid": {"solid": "box", "size": [4, 2, 3], "label": "Compressor skid"}}, "classes": {"CO": "skid"}}
    t = scene.scene(conn, k, pack=_Pack(mine))
    assert {i["shape"] for i in t["items"] if i["label"].startswith("20-K-")} == {"skid"}
    assert t["shapes"]["skid"]["label"] == "Compressor skid"
    assert [(i["id"], i["x"], i["z"]) for i in t["items"]] == [(i["id"], i["x"], i["z"]) for i in s["items"]]


def test_a_bundle_naming_a_shape_the_viewer_cant_draw_is_refused():
    with pytest.raises(ValueError, match="solid"):
        shapes.checked({"shapes": {"skid": {"solid": "teapot", "size": [1, 1, 1], "label": "Skid"}}})
    with pytest.raises(ValueError, match="not a shape"):
        shapes.checked({"classes": {"CO": "skid"}})


def test_the_api_serves_the_scene_and_refuses_an_unknown_link_type(compression):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    _conn, k, s = compression
    client = TestClient(app)
    got = client.get("/scene", params={"at": k}).json()
    assert got["focus"] == k and len(got["items"]) == len(s["items"])
    only = client.get("/scene", params={"at": k, "rels": "pipes_to"}).json()
    assert only["rels"] == ["pipes_to"] and {link["rel"] for link in only["links"]} == {"pipes_to"}
    assert client.get("/scene", params={"rels": "nonsense"}).status_code == 422

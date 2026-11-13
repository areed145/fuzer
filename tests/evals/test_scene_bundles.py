"""P5.16a on the other two bundles: a gas plant and a pipeline terminal draw from the register alone, with the same
code and the platform's shape table."""

from fuzer.graph import scene


def _check(conn):
    s = scene.scene(conn)
    assert s["root"] and s["items"] and s["links"], "the main facility draws"
    assert s["counts"]["no_shape"] < len(s["items"]) / 2, "most items have a shape of their own"
    places = [(i["x"], i["z"]) for i in s["items"]]
    assert len(places) == len(set(places))
    busiest = max((p for p in s["plates"] if p["area"]), key=lambda p: p["items"])
    one = scene.scene(conn, busiest["id"])
    assert one["root"]["id"] == busiest["id"] and [p["id"] for p in one["plates"] if p["area"]] == [busiest["id"]]
    assert any(i["placed"] == "flow order" for i in one["items"])
    return s, one


def test_kestrel_draws(kestrel_db):
    s, _one = _check(kestrel_db)
    assert "compressor" in {i["shape"] for i in s["items"]}


def test_harrier_draws_with_no_wells(harrier_db):
    s, _one = _check(harrier_db)
    assert not [i for i in s["items"] if i["kind"] == "well"]
    assert "pump" in {i["shape"] for i in s["items"]}

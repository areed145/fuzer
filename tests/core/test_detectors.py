"""The detector registry (P1.2): each detector is declared once; one that lacks its inputs says so and does not run."""

from pathlib import Path

import pytest

from fuzer import reference
from fuzer.core import detectors, settings
from fuzer.core.pack import load_pack
from fuzer.evals import replay
from fuzer.ops import sre

ROOT = Path(__file__).parents[2]
REGISTRY = detectors.registry()


@pytest.mark.unit
@pytest.mark.parametrize("d", REGISTRY.values(), ids=list(REGISTRY))
def test_a_detector_says_what_it_is_what_it_raises_and_where_it_is_scored(d):
    assert d.title and d.kind in detectors.KINDS and d.cadence in detectors.CADENCES and d.raises
    assert all(k in settings.REGISTRY for k in d.params), "a detector's parameters are registered settings"
    kind, _, where = (d.eval or "").partition(":")
    assert kind in ("replay", "test"), "a detector names its eval"
    if kind == "replay":
        assert where in replay.DETECTORS
    else:
        path, _, name = where.partition("::")
        assert f"def {name}(" in (ROOT / path).read_text(), where


@pytest.mark.unit
def test_the_window_detectors_are_the_registry_not_a_fixed_list():
    names = [d.name for d in detectors.window()]
    assert names == ["excursions", "stops", "steps", "drifts", "instruments", "models", "hydraulics", "multivariate"]
    assert {f"sre:{n}" for n in names} < set(replay.DETECTORS) and not hasattr(sre, "DETECTORS")


@pytest.mark.unit
def test_adding_a_detector_is_one_registered_function():
    @detectors.register("seal_pots", title="A seal pot losing level", kind=detectors.RULE, cadence="daily",
                        raises=("machine_health",), classes=("pump",), roles=("level",),
                        needs={"a seal pot level": "select 1 where false"})
    def seal_pots(conn):
        """Barrier fluid level falling between top-ups."""
        return {"raised": []}

    try:
        page = reference.detectors_md()
        assert "| [`seal_pots`](#seal_pots) A seal pot losing level | Rule | Once a day | `machine_health` | pump |" in page
        assert "Barrier fluid level falling between top-ups." in page and "- **Needs:** a seal pot level" in page
        with pytest.raises(detectors.DetectorError, match="two detectors"):
            detectors.register("seal_pots", title="Another", kind=detectors.RULE, cadence="daily")(lambda conn: None)
        with pytest.raises(detectors.DetectorError, match="kind"):
            detectors.register("x", title="X", kind="guess", cadence="daily")
    finally:
        detectors.unregister("seal_pots")
    assert "seal_pots" not in reference.detectors_md()


@pytest.mark.integration
def test_a_detector_missing_its_inputs_reports_not_applicable_and_the_run_is_kept(okan_db):
    conn, pack, called = okan_db.conn, load_pack("okan"), []

    @detectors.register("seal_pots", title="A seal pot losing level", kind=detectors.RULE, cadence="daily",
                        raises=("machine_health",), found=lambda out: len(out["raised"]),
                        needs={"historian points": detectors.POINTS, "a seal pot level": "select 1 where false"})
    def seal_pots(conn, pack):
        called.append(pack.name)
        return {"raised": ["P-101"]}

    try:
        out = detectors.run(conn, pack, "seal_pots")
        assert out == {"status": "not_applicable", "missing": ["a seal pot level"],
                       "note": "not applicable: missing a seal pot level"} and not called
        row = next(d for d in detectors.listing(conn, pack) if d["name"] == "seal_pots")
        assert not row["applicable"] and row["note"] == out["note"]
        assert row["last_run"]["status"] == "not_applicable" and row["last_run"]["note"] == out["note"]

        REGISTRY["seal_pots"].needs.pop("a seal pot level")  # the input arrives
        assert detectors.run(conn, pack, "seal_pots") == {"raised": ["P-101"]} and called == ["okan"]
        last = detectors.last_runs(conn)["seal_pots"]
        assert last["status"] == "ran" and last["found"] == 1

        here = {d["name"]: d for d in detectors.listing(conn, pack)}  # Okan has what its own detectors need
        assert all(here[n]["applicable"] for n in ("excursions", "stops", "compressor_trips", "machine_health")), here
    finally:
        detectors.unregister("seal_pots")
        conn.rollback()

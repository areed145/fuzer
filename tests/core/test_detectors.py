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

        detectors.get("seal_pots").needs.pop("a seal pot level")  # the input arrives
        assert detectors.run(conn, pack, "seal_pots") == {"raised": ["P-101"]} and called == ["okan"]
        last = detectors.last_runs(conn)["seal_pots"]
        assert last["status"] == "ran" and last["found"] == 1

        here = {d["name"]: d for d in detectors.listing(conn, pack)}  # Okan has what its own detectors need
        assert all(here[n]["applicable"] for n in ("excursions", "stops", "compressor_trips", "machine_health")), here
    finally:
        detectors.unregister("seal_pots")
        conn.rollback()


@pytest.mark.unit
def test_a_parameter_read_per_class_is_one_of_the_detectors_params():
    with pytest.raises(detectors.DetectorError, match="per equipment class"):
        detectors.register("x", title="X", kind=detectors.RULE, cadence="daily", by_class=("sre.drift_rise",))
    assert REGISTRY["drifts"].by_class == ("sre.drift_rise",)


@pytest.mark.integration
def test_a_detector_is_switched_and_tuned_per_class_on_one_asset_with_who_and_why(okan_db):
    conn, pack, p = okan_db.conn, load_pack("okan"), settings.project()
    classes = {c["code"]: c for c in detectors.asset_classes(conn)}
    assert {"CO", "PU"} <= set(classes) and classes["CO"]["units"] > 0, classes
    co = next(u for u, c in detectors.classes_of(conn, [r["src"] for r in conn.execute(
        "select c.src from edge c join entity_label l on l.fuzer_id = c.dst where c.rel = 'classified_as' "
        "and l.label like 'ISO14224:CO%' limit 5")]).items() if c == "CO")
    assert co
    try:
        detectors.tune(conn, "drifts", "sre.drift_rise", 0.3, cls="CO", by="ana", reason="compressors drift slowly",
                       project_name=p)
        assert detectors.param(conn, "sre.drift_rise", "CO") == 0.3
        assert detectors.param(conn, "sre.drift_rise", "PU") == detectors.param(conn, "sre.drift_rise") == 0.05
        h = settings.history(conn, "sre.drift_rise@CO")[0]
        assert (h["new"], h["by"], h["reason"], h["scope"]) == (0.3, "ana", "compressors drift slowly", f"project:{p}")
        with pytest.raises(detectors.DetectorError, match="no equipment class"):
            detectors.tune(conn, "drifts", "sre.drift_rise", 0.3, cls="ZZ", by="ana", project_name=p)
        with pytest.raises(detectors.DetectorError, match="same for every class"):
            detectors.tune(conn, "steps", "signals.step_z", 5, cls="CO", by="ana", project_name=p)
        with pytest.raises(detectors.DetectorError, match="not tuned by"):
            detectors.tune(conn, "drifts", "sre.step_minutes", 5, by="ana", project_name=p)

        detectors.switch(conn, "machine_health", False, by="ana", reason="screen under review", project_name=p)
        assert detectors.run(conn, pack, "machine_health") == {"status": "off", "note": "switched off"}
        assert detectors.last_runs(conn)["machine_health"]["status"] == "off"
        conn.execute("insert into ops_eval (detector, since, until, scores) values ('sre:drifts', '2024-11-01', "
                     "'2024-11-08', '{\"recall\": 0.8, \"precision\": 0.9, \"false_alarms_per_week\": 1.5}')")
        rows = {d["name"]: d for d in detectors.listing(conn, pack, p)}
        assert not rows["machine_health"]["on"] and rows["machine_health"]["switched"]["updated_by"] == "ana"
        assert rows["drifts"]["on"] and rows["drifts"]["score"]["recall"] == 0.8
        rise = next(s for s in rows["drifts"]["settings"] if s["key"] == "sre.drift_rise")
        assert rise["by_class"] and [(c["qualifier"], c["value"]) for c in rise["classes"]] == [("CO", 0.3)]
        assert rows["compressor_trips"]["score"] is None  # scored by a test, not replay
    finally:
        conn.rollback()
        detectors.tune(conn, "drifts", "sre.drift_rise", None, cls="CO", by="ana", project_name=p, reset=True)
        detectors.switch(conn, "machine_health", True, by="ana", project_name=p)
        conn.execute("delete from ops_eval where detector = 'sre:drifts' and since = '2024-11-01'")
        conn.commit()

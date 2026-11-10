"""Source quality (#225): profiles kept per load with drift, load checks that hold a dataset, each system's
number / date / text formats read before mapping, coverage and match statistics after rebuilds, the health
summary, rules in the database, the impact of a mapping change and the lineage of a golden value."""

import csv
import shutil
import tempfile
from pathlib import Path

import pytest

from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import catalog, impact, ingest, quality, rules
from fuzer.sources.formats import Formats

PACK = load_pack("okan")


@pytest.fixture
def data(okan_data):
    d = Path(tempfile.mkdtemp(prefix="fzq"))
    shutil.copytree(okan_data, d, dirs_exist_ok=True)
    return d


@pytest.fixture
def conn():
    with db.connect() as c:
        db.reset(c)
        yield c


def _rewrite(path: Path, fn) -> None:
    rows = list(csv.DictReader(path.open()))
    rows = [fn(r) for r in rows]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def test_formats_read_decimal_commas_dates_and_timezones():
    f = Formats(",", "%d.%m.%Y %H:%M", "Europe/Oslo", "UTC")
    assert f.row({"v": "1.234,5", "w": "12,0", "t": "31.12.2024 14:00", "s": "A.B", "n": "7"}) == \
        {"v": "1234.5", "w": "12.0", "t": "2024-12-31T13:00", "s": "A.B", "n": "7"}
    assert Formats(None, "%d/%m/%Y").row({"d": "05/02/2024", "tag": "20-PT-1001"}) == {"d": "2024-02-05", "tag": "20-PT-1001"}
    assert not Formats().active


def test_profiles_and_load_checks(conn, data):
    ingest.ingest(conn, data)
    p = quality.profiles(conn, "sap_equipment")[0]
    assert p["rows"] > 0 and p["columns"]["equnr"]["fill_pct"] == 100.0 and p["key"]["duplicates"] == 0

    # a blocking not_null check fails: the load is held, the records stay as they were, a review item says why
    _rewrite(data / "sap_equipment.csv", lambda r: {**r, "manufacturer": ""})
    chk = quality.add_check(conn, "sap_equipment", "not_null", "ana", "manufacturer", blocking=True)
    before = conn.execute("select count(*) as n from source_record where source = 'sap' and record_type = 'equipment' "
                          "and payload->>'manufacturer' <> ''").fetchone()["n"]
    out = ingest.ingest(conn, data, ["sap_equipment"])
    assert out["held"] == ["sap_equipment"] and catalog.runs(conn, "sap_equipment")[0]["status"] == "held"
    assert any(d["change"] == "fill dropped" and d["column"] == "manufacturer"  # what was delivered is profiled
               for d in out["profile_drift"]["sap_equipment"])
    assert conn.execute("select count(*) as n from source_record where source = 'sap' and record_type = 'equipment' "
                        "and payload->>'manufacturer' <> ''").fetchone()["n"] == before
    assert conn.execute("select status from review_item where kind = 'check' and subject = 'sap_equipment'"
                        ).fetchone()["status"] == "open"
    assert quality.checks(conn, "sap_equipment")[0]["last_ok"] is False

    # not blocking: it loads and the result is kept
    quality.remove_check(conn, chk["id"], "ana")
    quality.add_check(conn, "sap_equipment", "not_null", "ana", "manufacturer", {"max_pct": 5})
    out = ingest.ingest(conn, data, ["sap_equipment"])
    assert out["loaded"] == ["sap_equipment"]
    assert quality.checks(conn, "sap_equipment")[0]["last_value"] == 100.0
    assert conn.execute("select status from review_item where kind = 'check' and subject = 'sap_equipment'"
                        ).fetchone()["status"] == "stale"  # the hold is over

    with pytest.raises(quality.CheckError, match="needs a column"):
        quality.add_check(conn, "sap_equipment", "range", "ana")


def test_duplicate_keys_hold_a_load(conn, data):
    ingest.ingest(conn, data)
    rows = list(csv.DictReader((data / "sap_floc.csv").open()))
    with (data / "sap_floc.csv").open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys()).writerow(rows[0])
    out = ingest.ingest(conn, data, ["sap_floc"])
    assert out["held"] == ["sap_floc"]
    run = catalog.runs(conn, "sap_floc")[0]
    assert run["status"] == "held" and run["detail"]["checks"][0]["kind"] == "duplicate keys"


def test_a_dataset_can_load_the_latest_row_of_a_repeated_key(conn, data):
    """#459: instead of holding, the load keeps the last row read for each repeated key, and the run lists the rows
    it left out."""
    ingest.ingest(conn, data)
    path = data / "sap_floc.csv"
    rows = list(csv.DictReader(path.open()))
    newer = {**rows[0], "description": rows[0]["description"] + " (renamed)"}
    with path.open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys()).writerow(newer)
    catalog.update_dataset(conn, "sap_floc", "ana", reason="SAP re-exports a renamed location", duplicates="keep_latest")
    out = ingest.ingest(conn, data, ["sap_floc"])
    assert out["loaded"] == ["sap_floc"] and not out["held"] and out["updated"] == 1
    run = catalog.runs(conn, "sap_floc")[0]
    dropped = run["detail"]["duplicates_dropped"]
    assert run["status"] == "done" and dropped["rows"] == 1 and dropped["keys"] == 1
    assert dropped["shown"][0]["row"]["description"] == rows[0]["description"]  # the older row is the one dropped
    with pytest.raises(catalog.CatalogError, match="duplicates must be one of"):
        catalog.update_dataset(conn, "sap_floc", "ana", duplicates="keep_first")
    conn.rollback()
    catalog.update_dataset(conn, "sap_floc", "ana", duplicates="hold")  # the module's other tests expect a hold
    conn.commit()


def test_system_formats_and_encoding_apply_on_load(conn, data):
    catalog.seed(conn)
    catalog.update_system(conn, "jde", "ana", decimal=",", encoding="latin-1")
    path = data / "jde_assets.csv"
    rows = [{"asset_number": "J1", "description": "Pompe à huile", "manufacturer": "Acme", "model": "P-1",
             "serial": "SN1"}]
    with path.open("w", newline="", encoding="latin-1") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    ingest.ingest(conn, data, ["jde_assets"])
    got = [r["payload"]["description"] for r in conn.execute(
        "select payload from source_record where source = 'jde'")]
    assert "Pompe à huile" in got


def test_rules_lay_over_the_pack(conn):
    catalog.seed(conn)
    p = rules.apply(conn, PACK)
    assert p.survivorship["default"]["priority"][:2] == ["review", PACK.survivorship["default"]["priority"][1]]
    rules.set_rank(conn, "maximo", 1, "ana")
    rules.set_rule(conn, "field", "manufacturer", {"priority": ["review", "maximo"], "review_on_conflict": False}, "ana")
    rules.set_rule(conn, "match", "equipment", {"auto_accept": 0.97}, "ana")
    p = rules.apply(conn, PACK)
    assert p.survivorship["default"]["priority"][1] == "maximo"
    assert p.rule("manufacturer")["priority"] == ["review", "maximo"]
    assert p.matching["equipment"]["auto_accept"] == 0.97
    assert p.matching["equipment"]["review_min"] == PACK.matching["equipment"]["review_min"]
    rules.seed(conn, PACK)  # an edited rule doesn't follow the pack
    assert rules.apply(conn, PACK).matching["equipment"]["auto_accept"] == 0.97
    with pytest.raises(rules.RuleError):
        rules.set_rule(conn, "match", "equipment", {"auto_accept": 0.5, "review_min": 0.8}, "ana")
    assert len(catalog.audit(conn, "rule")) == 2 and catalog.audit(conn, "system")[0]["new"] == {"rank": 1}


def test_after_a_rebuild(conn, data):
    ingest.ingest(conn, data)
    pipeline.rebuild(conn, PACK)
    stats = quality.matching(conn, "sap")
    assert stats and stats[0]["stats"]["records"] > 0 and 0 < stats[0]["stats"]["coverage"]["pct"] <= 100
    assert stats[0]["stats"]["golden_wins"] > 0 and "by_method" in stats[0]["stats"]
    h = {x["dataset"]: x for x in quality.health(conn)}
    assert h["sap_equipment"]["state"] == "ok" and h["sap_equipment"]["coverage_pct"] is not None
    catalog.update_dataset(conn, "sap_equipment", "ana", expected_hours=0.0001)
    assert quality.health(conn, "sap_equipment")[0]["problems"] == ["stale"]
    conn.rollback()

    # impact: dropping a field from the equipment mapping changes fields, and flips golden values SAP wins
    text = catalog.versions(conn, "sap", "equipment")[0]["yaml"]
    assert "- model\n" in text or "model," in text
    changed = text.replace("- model\n", "") if "- model\n" in text else text.replace("model, ", "")
    out = impact.preview(conn, PACK, changed)
    assert out["records"] > 0 and out["fields"]["changed"] > 0 and out["entities"]["added"] == 0
    assert out["golden_flips"]["count"] > 0
    same = impact.preview(conn, PACK, text)
    assert same["fields"]["changed"] == same["links"]["added"] == 0

    # lineage of a golden field
    fid = conn.execute("select g.fuzer_id from golden g where g.field = 'manufacturer' and g.source = 'sap' limit 1"
                       ).fetchone()["fuzer_id"]
    w = impact.why(conn, PACK, fid, "manufacturer")
    sap = next(a for a in w["assertions"] if a["source"] == "sap")
    rtype = sap["record"].split(":")[1]
    mine = next(d["name"] for d in catalog.datasets(conn).values() if d["source"] == "sap" and d["record_type"] == rtype)
    assert w["source"] == "sap" and sap["wins"] and sap["mapping_version"]["id"] and sap["run"]["dataset"] == mine
    assert w["priority"][0] == "review"


def test_health_check_outcome():
    assert quality.health_check_outcome({"problems": [], "failing_checks": []}) == (True, False)
    assert quality.health_check_outcome({"problems": ["stale"], "failing_checks": []}) == (False, False)
    assert quality.health_check_outcome({"problems": ["checks failing"],
                                         "failing_checks": [{"blocking": True}]}) == (False, True)
    assert quality.health_check_outcome({"problems": ["failed"], "failing_checks": []}) == (False, True)

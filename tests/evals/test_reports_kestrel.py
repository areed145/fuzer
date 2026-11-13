"""Regulatory and emissions reports as workflow outputs (P7.6a, #954): Kestrel's monthly flare and emissions report,
drafted from its metered volumes, its flare flow meter and its flare episodes, every figure drilling to its records, a
figure resting on estimated or missing data marked, and the draft reviewed as work."""

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from fuzer.analysis import flare
from fuzer.core import pack as packs
from fuzer.graph import operations
from fuzer.ops import pipeline, reports
from fuzer.sources import ingest
from fuzer.sources import packs as source_packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "flare_emissions_monthly"


@pytest.fixture(scope="module")
def kestrel(kestrel_db, tmp_path_factory):
    """Kestrel with its gas measurement system's pack installed and loaded, and the flare watch run."""
    conn = kestrel_db
    folder = tmp_path_factory.mktemp("measurement") / "measurement"
    shutil.copytree(KESTREL / "measurement", folder)
    source_packs.install(conn, (KESTREL / "packs" / "measurement.yaml").read_text(), "ana",
                         config={"export": {"path": str(folder)}}, data_dir=folder)
    ingest.ingest(conn, folder, exports=["daily_volumes"], trigger="test")
    pack = packs.current_pack(conn)
    pipeline.rebuild(conn, pack)
    flare.watch(conn)
    conn.commit()
    yesterday = date.fromisoformat(operations.as_of(conn)) - timedelta(days=1)
    yield {"conn": conn, "pack": pack, "period": reports.period_of(yesterday), "yesterday": yesterday}
    conn.rollback()


def _figures(r: dict) -> dict:
    return {f["key"]: f for s in r["sections"] for f in s["figures"]}


def test_the_monthly_flare_report_is_drafted_from_its_records_with_estimates_marked_and_reviewed(kestrel):
    conn, pack, period, yesterday = kestrel["conn"], kestrel["pack"], kestrel["period"], kestrel["yesterday"]
    conn.execute("delete from regulatory_report")  # whatever another test in this module drafted
    out = reports.assemble(conn, pack, NAME, period)
    assert out["changed"] and out["version"] == 1 and out["item_id"]
    r = reports.get(conn, NAME, period)
    assert [s["title"] for s in r["sections"]] == ["Metered gas", "Flaring", "GHG inputs", "Flare events"]
    assert r["coverage"]["through"] == yesterday.isoformat()
    figs = _figures(r)

    # Each meter's figure is the sum of its daily volumes, each row drilling to the measurement record behind it.
    meters = [f for k, f in figs.items() if k.startswith("meter:")]
    assert {f["label"] for f in meters} == {f"Metered gas, {x}" for x in
                                           ("A1-FT-1002", "C1-FT-3005", "H1-FT-8010", "KR 12-03H", "KR 12-04H")}
    for f in meters:
        loaded = conn.execute("select sum(value) as v from measurement where fuzer_id = %s and quantity = 'volume' "
                              "and at >= %s and at <= %s", (f["subject"], f"{period}-01", yesterday)).fetchone()["v"]
        assert f["value"] == round(loaded, 1) and f["unit"] == "kSm3", f["label"]
        for row in f["rows"]:
            record = row["ref"].removeprefix("record:")
            assert conn.execute("select value from measurement where record = %s and quantity = 'volume'",
                                (record,)).fetchone()["value"] == row["value"]

    # The two wells' volumes the measurement system estimated yesterday are marked, in the figure and the draft.
    for well in ("KR 12-03H", "KR 12-04H"):
        f = next(f for f in meters if f["label"].endswith(well))
        assert f["basis"] == "estimated" and f["flags"] == [f"1 estimated: {yesterday.isoformat()}"]
        assert [x["label"] for x in f["rows"] if x["basis"] == "estimated"] == [yesterday.isoformat()]
    assert {m["label"] for m in r["marked"]} >= {"Metered gas, KR 12-03H", "Metered gas, KR 12-04H"}
    assert next(f for f in meters if f["label"].endswith("A1-FT-1002"))["basis"] == "measured"

    # The flare meter's daily volume, the highest on yesterday's episode; the flared energy is always an estimate.
    [flared] = [f for k, f in figs.items() if k.startswith("flare:")]
    assert flared["unit"] == "kSm3" and flared["basis"] == "measured" and flared["value"] > 0
    peak = max(flared["rows"], key=lambda x: x["value"])
    assert peak["label"] == yesterday.isoformat() and peak["ref"].endswith(f"@{yesterday.isoformat()}")
    assert figs["ghg:flared_volume"]["value"] == flared["value"]
    assert figs["ghg:flared_energy"]["basis"] == "estimated" and figs["ghg:flared_energy"]["value"] > 0

    # The episode is listed with its cause, and drills to the flare_event case that holds its records.
    episodes = figs["flare_events"]
    assert episodes["value"] >= 1
    for row in episodes["rows"]:
        item = int(row["ref"].removeprefix("case:"))
        assert conn.execute("select type from exception_record where item_id = %s", (item,)).fetchone()["type"] \
            == "flare_event"
    assert figs["flare_events_unknown"]["value"] == sum("cause unknown" in x["text"] for x in episodes["rows"])

    # The draft is review work for the HSE specialist, saying which figures rest on estimated or missing data.
    ex = conn.execute("select type, state, title, item_id from exception_record where type = 'regulatory_report' "
                      "and key = %s order by id desc limit 1", (f"{NAME}:{period}",)).fetchone()
    assert ex["state"] == "open" and ex["item_id"] == out["item_id"]
    assert "version 1 to review" in ex["title"] and "estimated or missing data" in ex["title"]
    assert conn.execute("select role from work_item where id = %s", (ex["item_id"],)).fetchone()["role"] \
        == "hse_specialist"

    # Nothing changed: no new version. A day's volume gone missing makes version 2, with that figure marked missing.
    assert reports.assemble(conn, pack, NAME, period)["changed"] is False
    [gone] = conn.execute("select m.record from measurement m join entity_label l on l.fuzer_id = m.fuzer_id "
                          "where l.label = 'A1-FT-1002' and m.quantity = 'volume' and m.at = %s",
                          (yesterday,)).fetchall()
    conn.execute("delete from measurement where record = %s", (gone["record"],))
    again = reports.assemble(conn, pack, NAME, period)
    assert again["changed"] and again["version"] == 2 and again["item_id"] == out["item_id"]
    a1 = next(f for f in _figures(reports.get(conn, NAME, period)).values() if f["label"].endswith("A1-FT-1002"))
    assert a1["basis"] == "missing" and a1["flags"] == [f"1 missing: {yesterday.isoformat()}"]
    assert reports.get(conn, NAME, period, version=1)["versions"] == [1, 2]
    listed = reports.listing(conn)
    assert [(x["template"], x["period"], x["version"]) for x in listed["reports"]] == [(NAME, period, 2)]
    conn.rollback()


def test_a_period_that_is_not_a_month_or_has_not_started_is_refused(kestrel):
    conn = kestrel["conn"]
    with pytest.raises(reports.ReportError, match="YYYY-MM"):
        reports.bounds(conn, "2026-13")
    with pytest.raises(reports.ReportError, match="hasn't started"):
        reports.bounds(conn, reports.period_of(kestrel["yesterday"] + timedelta(days=40)))
    with pytest.raises(reports.ReportError, match="no report template"):
        reports.assemble(conn, kestrel["pack"], "nothing", kestrel["period"])


def test_the_api_lists_and_shows_a_report_and_refuses_one_nobody_drafted(kestrel):
    from fastapi import HTTPException

    from fuzer.api import ops as ops_api

    conn, period = kestrel["conn"], kestrel["period"]
    reports.assemble(conn, kestrel["pack"], NAME, period)
    assert ops_api.regulatory_reports(50, conn)["templates"][0]["name"] == NAME
    shown = ops_api.regulatory_report(NAME, period, None, conn)
    assert shown["title"] == "Monthly flare and emissions report" and shown["review"]["state"] == "open"
    for template, p in ((NAME, "2001-01"), ("nothing", period)):
        with pytest.raises(HTTPException) as e:
            ops_api.regulatory_report(template, p, None, conn)
        assert e.value.status_code == 404


def test_a_person_approves_the_report_and_it_is_exported_and_submitted_as_kept(kestrel, tmp_path, monkeypatch):
    """P7.6b: approval is a fixed gate an agent can't pass, a marked figure needs the reviewer's word, the export
    says DRAFT until it is approved, and the submitted version is kept with the exact file sent."""
    import csv
    import hashlib
    import io

    from fuzer.core import gates
    from fuzer.ops import writeback
    from fuzer.sources import catalog
    from fuzer.workflow import engine

    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    conn, pack, period = kestrel["conn"], kestrel["pack"], kestrel["period"]
    conn.execute("delete from regulatory_report")
    draft = reports.assemble(conn, pack, NAME, period)
    v = draft["version"]

    out = reports.export(conn, NAME, period)
    assert out["filename"].endswith("-DRAFT.csv") and out["content"].startswith("DRAFT ")
    rows = list(csv.reader(io.StringIO(out["content"])))
    head = rows.index(["section", "figure", "row", "value", "unit", "basis", "record"])
    body = rows[head + 1:]
    assert any(r[1] == "Metered gas, KR 12-03H" and r[2] == "" and r[5] == "estimated" for r in body)
    assert all(r[6].startswith(("record:", "point:", "case:")) for r in body if r[2] and r[6])

    with pytest.raises(gates.GateRefused):
        reports.approve(conn, NAME, period, "agent:reporter")
    conn.rollback()
    with pytest.raises(reports.ReportError, match="rest on estimated or missing data"):
        reports.approve(conn, NAME, period, "hana")
    with pytest.raises(reports.ReportError, match="no approved version"):
        reports.submit(conn, NAME, period, "hana")
    with pytest.raises(reports.ReportError, match="not the latest"):
        reports.approve(conn, NAME, period, "hana", version=v + 1)

    note = "The wells' estimates are the measurement system's own; the corrections follow in next month's report."
    ok = reports.approve(conn, NAME, period, "hana", note=note)
    assert (ok["status"], ok["approved_by"], ok["approval_note"]) == ("approved", "hana", note)
    ex = conn.execute("select state, item_id from exception_record where type = 'regulatory_report' and key = %s "
                      "order by id desc limit 1", (f"{NAME}:{period}",)).fetchone()
    assert ex["state"] != "open"
    assert conn.execute("select state from work_item where id = %s", (ex["item_id"],)).fetchone()["state"] == "done"
    with pytest.raises(reports.ReportError, match="already approved"):
        reports.approve(conn, NAME, period, "hana")
    final = reports.export(conn, NAME, period)
    assert "DRAFT" not in final["filename"] and final["content"].startswith("Monthly flare")
    assert ["approved", "hana"] == next(r for r in csv.reader(io.StringIO(final["content"])) if r[0] == "approved")[:2]

    # Sent through the write-back queue to the regulator's system: its approver's queue holds the exact file.
    engine.set_person(conn, "hana", ["hse_specialist"])
    catalog.add_system(conn, "regulator", "test", files=False, writeback={"accepts": ["regulatory_report"]})
    sent = reports.submit(conn, NAME, period, "hana", system="regulator")
    assert sent["status"] == "submitted" and sent["submitted"]["via"] == "writeback"
    assert sent["submitted"]["sha256"] == hashlib.sha256(final["content"].encode()).hexdigest()
    wb = writeback.get(conn, sent["submitted"]["writeback_id"])
    assert (wb["kind"], wb["state"], wb["approver_role"]) == ("regulatory_report", "awaiting_approval", "hse_specialist")
    assert wb["payload"]["content"] == final["content"]
    writeback.approve(conn, wb["id"], "hana")
    assert writeback.send(conn, wb["id"])["state"] == "sent"
    conn.commit()
    with pytest.raises(reports.ReportError, match="already submitted"):
        reports.submit(conn, NAME, period, "hana")

    kept = reports.get(conn, NAME, period, version=v)
    assert kept["status"] == "submitted" and kept["submitted"]["file"] == final["filename"]
    assert "content" not in kept["submitted"]
    stored = conn.execute("select submitted from regulatory_report where template = %s and period = %s and version = %s",
                          (NAME, period, v)).fetchone()["submitted"]
    assert stored["content"] == final["content"] and stored["at"]
    # Drafting again with the records unchanged leaves the submitted version as it is.
    assert reports.assemble(conn, pack, NAME, period)["changed"] is False


def test_the_obligations_calendar_drafts_ahead_and_raises_work_for_one_coming_due_with_nothing_started(kestrel):
    """P7.6c: an obligation whose window has opened is drafted from its records; one with no draft in progress (it
    may not be drafted ahead) raises report_due for the HSE specialist, which clears once a draft is in progress."""
    conn, pack, yesterday = kestrel["conn"], kestrel["pack"], kestrel["yesterday"]
    today = yesterday + timedelta(days=1)
    last = reports.period_of(today.replace(day=1) - timedelta(days=1))  # last month, a whole month
    in_five = (today + timedelta(days=5) - date.fromisoformat(f"{reports._shift(last, 1)}-01")).days + 1
    conn.execute("delete from regulatory_report where period = %s", (last,))
    conn.execute("delete from report_obligation")
    with pytest.raises(reports.ReportError, match="no report template"):
        reports.set_obligation(conn, "x", "nothing", "ana")

    reports.set_obligation(conn, "flaring_return", NAME, "ana", regulator="the regulator", due_days=in_five,
                           lead_days=30, draft_ahead=False)
    [c] = reports.calendar(conn, today)
    assert (c["period"], c["due"], c["days_left"], c["report"]) == (last, (today + timedelta(days=5)).isoformat(), 5,
                                                                    None)
    out = reports.run_calendar(conn, pack, today)
    assert out["drafted"] == [] and [r["key"] for r in out["raised"]] == [f"flaring_return:{last}"]
    ex = conn.execute("select state, title, item_id from exception_record where type = 'report_due' and key = %s",
                      (f"flaring_return:{last}",)).fetchone()
    assert ex["state"] == "open" and "in 5 days: not started" in ex["title"]
    assert conn.execute("select role from work_item where id = %s", (ex["item_id"],)).fetchone()["role"] \
        == "hse_specialist"
    assert reports.run_calendar(conn, pack, today)["raised"][0]["new"] is False  # refreshed, not raised twice

    # Allowed to draft ahead, the calendar drafts last month from its records and the work clears.
    reports.set_obligation(conn, "flaring_return", NAME, "ana", regulator="the regulator", due_days=in_five,
                           lead_days=30, draft_ahead=True)
    out = reports.run_calendar(conn, pack, today)
    assert out["drafted"] == [f"flaring_return:{last}"] and out["raised"] == []
    assert out["cleared"] == [f"flaring_return:{last}"]
    r = reports.get(conn, NAME, last)
    assert r["version"] == 1 and r["coverage"]["partial"] is False and r["review"]["state"] == "open"

    # Past its due date and not submitted, it is work again, overdue.
    late = today + timedelta(days=6)
    out = reports.run_calendar(conn, pack, late)
    [raised] = out["raised"]
    title = conn.execute("select title from exception_record where type = 'report_due' and key = %s and "
                         "state = 'open'", (raised["key"],)).fetchone()["title"]
    assert f"was due {(today + timedelta(days=5)).isoformat()}: version 1 draft, not submitted" in title
    # Approved and submitted, it is off the calendar and its work clears.
    reports.approve(conn, NAME, last, "hana", note="Checked against the meter tickets.")
    reports.submit(conn, NAME, last, "hana")
    out = reports.run_calendar(conn, pack, late)
    assert last not in {c["period"] for c in out["due"]} and raised["key"] in out["cleared"]
    assert reports.obligations(conn, late)["obligations"][0]["name"] == "flaring_return"

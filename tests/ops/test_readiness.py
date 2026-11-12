"""Ready to dispatch (P5.5, #680): a job's spares from its equipment's BOM, stock and purchase orders, and its crew
from the work center its work orders name, and its permits to work, on Kestrel and Okan, read from the records and never from the equipment
type."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs
from fuzer.ops import readiness

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_readiness"


@pytest.fixture(scope="module")
def kestrel():
    from fuzer.ops import pipeline
    from fuzer.sources import ingest
    from fuzer.workflow import engine

    mp = pytest.MonkeyPatch()
    with db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres")) as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    mp.setenv("FUZER_DATABASE_URL", url)
    mp.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        first = bundles.init(conn, KESTREL, by="test")
        mp.setenv("FUZER_DATA_DIR", str(first["data"]))
        pack = packs.current_pack(conn)
        ingest.ingest(conn, Path(first["data"]))
        pipeline.rebuild(conn, pack)
        engine.load_defaults(conn)
        conn.commit()
        yield conn, pack
    mp.undo()
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_a_compressor_short_of_valves_waits_on_its_order_and_its_packing_has_none(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    k2001 = queries.resolve_ref(conn, pack, "B1-K-2001")
    out = readiness.check(conn, [k2001])
    assert out["ready"] is False
    spares = out["checks"][0]
    valves = next(p for p in spares["parts"] if p["label"] == "MAT-K2001-VLV")
    assert (valves["needed"], valves["stock"], valves["short"]) == (4, 2, 2)
    assert valves["orders"] == [{"po": "4500007101", "quantity": 2, "due": (date.today() + timedelta(days=3)).isoformat()}]
    rings = next(p for p in spares["parts"] if p["label"] == "MAT-K2001-RNG")
    assert rings["short"] == 0  # 8 in stock, 8 needed
    assert any("Compressor valve assembly" in b and "on order (4500007101)" in b for b in out["blockers"])
    assert any("Rod packing set" in b and "nothing on order" in b for b in out["blockers"])
    assert len(spares["parts"]) == 3


def test_equipment_with_its_parts_in_stock_is_ready_and_one_with_no_bom_says_so(kestrel):
    from fuzer.graph import queries

    conn, pack = kestrel
    fan = queries.resolve_ref(conn, pack, "B1-E-2010")  # 2 belts needed, 3 in stock
    assert readiness.check(conn, [fan])["ready"] is True
    out = readiness.check(conn, ["fz-nothing-here"])
    assert out["ready"] is True and out["checks"][0]["checked"] is False
    assert out["checks"][0]["note"].startswith("No BOM")
    assert [c["check"] for c in out["checks"]] == ["spares", "crew", "permits", "simops"]


@pytest.fixture(autouse=True)
def fresh(request):
    """Each test starts with nothing in the field or on the plan (tests run in random order)."""
    yield
    if "kestrel" in request.fixturenames:
        conn = request.getfixturevalue("kestrel")[0]
        conn.rollback()
        conn.execute("update exception_record set state = 'closed' where state = 'open'")
        conn.execute("update plan_item set status = 'cancelled' where status in ('proposed', 'scheduled', 'in_progress')")
        conn.commit()


@pytest.fixture
def client(kestrel, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api
    from fuzer.core.exceptions import TYPES, ExceptionType

    monkeypatch.setitem(TYPES, "walkdown_check", ExceptionType("walkdown_check", "Walkdown check",
                                                               audience=("ioc", "field")))
    app = FastAPI()
    app.include_router(ops_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: kestrel[0]
    return TestClient(app)


def job(conn, key: str, subject: str) -> int:
    from fuzer.core.exceptions import raise_exception

    return raise_exception(conn, "walkdown_check", key, f"Look at {key}", "olga", refs=[subject])["id"]


def test_a_blocked_job_goes_only_with_a_reason_and_the_reason_is_on_its_case(kestrel, client):
    from fuzer.graph import queries

    conn, pack = kestrel
    e = job(conn, "K-2001 valves", queries.resolve_ref(conn, pack, "B1-K-2001"))
    ready = client.get(f"/exceptions/{e}/readiness").json()
    assert ready["ready"] is False and any("on order (4500007101)" in b for b in ready["blockers"])
    r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred"})
    assert r.status_code == 409 and "Not ready to dispatch: Spares: " in r.text and "Say why" in r.text
    r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred",
                                                     "override": "inspect only, no parts needed today"})
    assert r.status_code == 200, r.text
    item = r.json()["item_id"]
    said = [c["body"] for c in conn.execute(
        "select data->>'text' as body from work_event where item_id = %s and kind = 'commented'", (item,))]
    assert any(s.startswith("Sent though not ready (Spares: ") and s.endswith("Why: inspect only, no parts needed today")
               for s in said), said
    fan = job(conn, "E-2010 belts", queries.resolve_ref(conn, pack, "B1-E-2010"))
    assert client.post(f"/exceptions/{fan}/dispatch", json={"by": "ioc-ann", "to": "fred"}).status_code == 200


def test_a_job_waits_for_a_free_crew_and_the_blocker_names_the_jobs_holding_it(kestrel, client):
    from fuzer.graph import queries

    conn, pack = kestrel
    gen = queries.resolve_ref(conn, pack, "G1-G-7006")  # its work orders go to ELEC, one electrician
    first = job(conn, "G-7006 load test", gen)
    crew = client.get(f"/exceptions/{first}/readiness").json()["checks"][1]
    assert crew["check"] == "crew" and crew["ready"] is True
    [elec] = crew["centers"]
    assert (elec["label"], elec["crew"], elec["busy"]) == ("ELEC", 1, 0)
    assert client.post(f"/exceptions/{first}/dispatch", json={"by": "ioc-ann", "to": "fred"}).status_code == 200
    second = job(conn, "G-7006 batteries", gen)
    out = client.get(f"/exceptions/{second}/readiness").json()
    assert out["ready"] is False
    assert f"Crew: Electrical technicians (ELEC): all 1 busy on open jobs: #{first} Look at G-7006 load test" in \
        out["blockers"]
    assert client.post(f"/exceptions/{second}/dispatch", json={"by": "ioc-ann", "to": "fred"}).status_code == 409
    # the first one is not held up by itself, and a compressor's mechanical crew (3) still has room
    assert client.get(f"/exceptions/{first}/readiness").json()["checks"][1]["ready"] is True
    k2001 = readiness.crew(conn, [queries.resolve_ref(conn, pack, "B1-K-2001")])
    assert [c["label"] for c in k2001["centers"]] == ["MECH"] and k2001["ready"] is True


def test_equipment_no_work_order_names_a_crew_for_is_not_blocked(kestrel):
    out = readiness.crew(kestrel[0], ["fz-nothing-here"])
    assert out["ready"] is True and out["checked"] is False and out["note"].startswith("No work orders")


def test_work_that_needs_a_permit_waits_for_one_in_date_with_its_isolations_in_place(kestrel, client):
    from fuzer.core import settings
    from fuzer.graph import queries

    conn, pack = kestrel
    settings.set(conn, "readiness.permit_types", "upset,iow_breach,walkdown_check", by="test")
    try:
        def permit(tag: str, key: str) -> dict:
            return readiness.permits(conn, [queries.resolve_ref(conn, pack, tag)], job(conn, key, queries.resolve_ref(
                conn, pack, tag)))

        k = permit("B1-K-2001", "K-2001 valves, permit")  # PTW-24101 is issued and in date, one isolation not applied
        assert k["needed"] and not k["ready"] and [p["label"] for p in k["permits"]] == ["PTW-24101"]
        [b] = k["blockers"]
        assert b.startswith("blocked: no valid permit (walkdown_check work on ") and "PTW-24101 isolations not in place" in b
        assert "ISO-24101-3 B1-K-2001 cylinders (planned)" in b and "ISO-24101-1" not in b
        assert permit("G1-G-7006", "G-7006 permit")["ready"]  # issued, in date, breaker locked out and verified
        pump = permit("C1-P-3002B", "P-3002B permit")
        assert not pump["ready"] and "PTW-24102 expired" in pump["blockers"][0]
        fan = permit("B1-E-2010", "E-2010 permit")
        assert not fan["ready"] and fan["blockers"][0].endswith("no permit covers this location")
        sep = permit("A1-V-1001", "V-1001 permit")  # the turnaround's entry permit is only requested
        assert not sep["ready"] and "PTW-24104 requested" in sep["blockers"][0]
        e = job(conn, "K-2001 permit via api", queries.resolve_ref(conn, pack, "B1-K-2001"))
        r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred"})
        assert r.status_code == 409 and "; blocked: no valid permit (walkdown_check" in r.text
    finally:
        settings.reset(conn, "readiness.permit_types", by="test")
    # a work type that needs none still shows the permits, without blocking on them
    out = readiness.permits(conn, [queries.resolve_ref(conn, pack, "B1-K-2001")],
                            job(conn, "K-2001 look only", queries.resolve_ref(conn, pack, "B1-K-2001")))
    assert out["ready"] and not out["needed"] and out["permits"][0]["why_not"].startswith("isolations not in place")


def test_starting_a_job_with_no_valid_permit_is_a_gate_with_its_reason_on_the_case(kestrel, client):
    from fuzer.core import settings
    from fuzer.graph import queries
    from fuzer.ops import exceptions

    conn, pack = kestrel
    settings.set(conn, "readiness.permit_types", "walkdown_check", by="test")
    try:
        e = job(conn, "P-3002B start", queries.resolve_ref(conn, pack, "C1-P-3002B"))  # its permit expired
        why = "no valid permit: PTW-24102 expired, area gas test clear, inspection only"
        assert client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred", "override": why}).is_success
        [mine] = [j for j in exceptions.field_jobs(conn, "fred") if j["id"] == e]
        assert mine["permit"]["blocked"] and mine["permit"]["blocker"].startswith("blocked: no valid permit")
        assert mine["permit"]["permit"]["label"] == "PTW-24102" and mine["permit"]["permit"]["why_not"] == "expired"
        r = client.post(f"/exceptions/{e}/report", json={"by": "fred", "status": "on_site"})
        assert r.status_code == 400 and "blocked: no valid permit" in r.text and "Say why it starts anyway" in r.text
        from fuzer.core.gates import GateRefused

        with pytest.raises(GateRefused):  # a fixed gate: only a named person starts without a permit
            client.post(f"/exceptions/{e}/report", json={"by": "agent:field", "status": "on_site", "override": "x"})
        conn.rollback()
        r = client.post(f"/exceptions/{e}/report", json={"by": "fred", "status": "on_site",
                                                       "override": "visual check only, nothing opened"})
        assert r.status_code == 200, r.text
        said = [c["body"] for c in conn.execute(
            "select data->>'text' as body from work_event where item_id = %s and kind = 'commented'",
            (r.json()["item_id"],))]
        assert any(s.startswith("Started without a valid permit (blocked: no valid permit") and
                   s.endswith("Why: visual check only, nothing opened") for s in said)
        # with a valid permit and its isolations verified, it just starts
        ok = job(conn, "G-7006 start", queries.resolve_ref(conn, pack, "G1-G-7006"))
        client.post(f"/exceptions/{ok}/dispatch", json={"by": "ioc-ann", "to": "fred", "override": "crew"})
        assert client.post(f"/exceptions/{ok}/report", json={"by": "fred", "status": "on_site"}).status_code == 200
    finally:
        settings.reset(conn, "readiness.permit_types", by="test")


def test_hot_work_beside_a_line_break_is_flagged_on_plan_and_at_dispatch(kestrel, client):
    from datetime import UTC, datetime

    from fuzer.graph import queries
    from fuzer.ops import plan

    conn, pack = kestrel
    reboiler, contactor = (queries.resolve_ref(conn, pack, t) for t in ("C1-H-3008", "C1-V-3001"))
    now = datetime.now(UTC)
    burner = plan.propose(conn, "maintenance", "Weld reboiler fire tube support", "planner-pat",
                          now - timedelta(hours=2), now + timedelta(hours=6), refs=[reboiler], status="scheduled")
    manway = plan.propose(conn, "maintenance", "Open contactor manway for tray check", "planner-pat",
                          now - timedelta(hours=1), now + timedelta(hours=5), refs=[contactor], status="scheduled")
    assert burner["area_id"] and burner["area_id"] == manway["area_id"]  # both in the TEG dehydration area
    shown = {i["id"]: i for i in plan.items(conn, subject=None)}
    said = [c["say"] for c in shown[burner["id"]]["clashes"] if c["kind"] == "simops"]
    assert len(said) == 1 and said[0].startswith("Hot work near a line break in ")
    assert "hot work (PTW-24105) for this job and line break (PTW-24106)" in said[0]
    assert [c["with"] for c in shown[manway["id"]]["clashes"] if c["kind"] == "simops"] == [burner["id"]]
    # the IOC sending someone to the reboiler now hears it before they go
    e = job(conn, "Reboiler burner flame-out", reboiler)
    ready = client.get(f"/exceptions/{e}/readiness").json()
    sim = next(c for c in ready["checks"] if c["check"] == "simops")
    assert not sim["ready"] and {c["kind"] for c in sim["conflicts"]} == {"same_asset", "simops"}
    assert any(b.startswith("Hot work near a line break in ") for b in sim["blockers"])
    assert any("“Weld reboiler fire tube support” is on" in b for b in sim["blockers"])
    r = client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred"})
    assert r.status_code == 409 and "Simultaneous operations: Hot work near a line break" in r.text


def test_a_job_handed_over_at_shift_change_stays_with_the_outgoing_operator_until_accepted(kestrel, client):
    from fuzer.graph import queries
    from fuzer.ops import exceptions

    conn, pack = kestrel
    e = job(conn, "K-2001 valve change, shift change", queries.resolve_ref(conn, pack, "B1-K-2001"))
    assert client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred", "reason": "change the valve",
                                                        "override": "parts on site"}).is_success
    assert client.post(f"/exceptions/{e}/report", json={"by": "fred", "status": "on_site"}).is_success
    r = client.post(f"/exceptions/{e}/handover", json={"by": "gina", "to": "fred", "done": "x", "left": "y"})
    assert r.status_code == 400 and "only whoever has the job" in r.text
    r = client.post(f"/exceptions/{e}/handover", json={"by": "fred", "to": "gina", "done": "isolated and blown down",
                                                     "left": "fit new discharge valve on cylinder 3",
                                                     "unsafe": "cylinder 3 cover is off"})
    assert r.status_code == 200, r.text
    # with gina, at the top, the outgoing note first; still fred's until she accepts it
    [top, *_] = exceptions.field_jobs(conn, "gina")
    assert top["id"] == e and top["handover"]["mine_to_accept"] and top["handover"]["from"] == "fred"
    assert (top["handover"]["done"], top["handover"]["left"], top["handover"]["unsafe"]) == (
        "isolated and blown down", "fit new discharge valve on cylinder 3", "cylinder 3 cover is off")
    assert top["handover"]["permit"]["label"] == "PTW-24101"  # the permit it runs under, as it stood
    [mine] = [j for j in exceptions.field_jobs(conn, "fred") if j["id"] == e]
    assert mine["handover"]["waiting_on"] == "gina" and not mine["queued"]
    assert client.post(f"/exceptions/{e}/handover/accept", json={"by": "otto"}).status_code == 400
    assert client.post(f"/exceptions/{e}/handover/accept", json={"by": "gina"}).is_success
    [taken] = [j for j in exceptions.field_jobs(conn, "gina") if j["id"] == e]
    assert taken["handover"] is None and not taken["queued"]
    assert not [j for j in exceptions.field_jobs(conn, "fred") if j["id"] == e]
    item = taken["item_id"]
    said = [c["body"] for c in conn.execute(
        "select data->>'text' as body from work_event where item_id = %s and kind = 'commented' order by id", (item,))]
    assert any(s.startswith("Handover to gina: done: isolated and blown down; left: fit new discharge valve") and
               "unsafe to leave: cylinder 3 cover is off" in s for s in said)
    assert said[-1] == "Accepted the handover from fred"
    # what gina can't finish goes back past fred, who is off shift, to the IOC that sent it
    assert client.post(f"/exceptions/{e}/report", json={"by": "gina", "status": "could_not_do",
                                                      "note": "wrong valve delivered"}).is_success
    holder = conn.execute("select coalesce((select assignee from work_token where item_id = %(i)s and kind = 'user' "
                          "and state in ('ready', 'waiting') limit 1), (select assignee from work_item where id = %(i)s))"
                          " as a", {"i": item}).fetchone()["a"]
    assert holder == "ioc-ann"


def test_the_shift_handover_lists_jobs_in_the_field_and_open_permits_from_the_records(kestrel, client):
    from datetime import UTC, datetime

    from fuzer.graph import queries
    from fuzer.ops import handover

    conn, pack = kestrel
    e = job(conn, "G-7006 load test, at shift change", queries.resolve_ref(conn, pack, "G1-G-7006"))
    client.post(f"/exceptions/{e}/dispatch", json={"by": "ioc-ann", "to": "fred", "override": "crew"})
    client.post(f"/exceptions/{e}/report", json={"by": "fred", "status": "on_site"})
    client.post(f"/exceptions/{e}/handover", json={"by": "fred", "to": "gina", "done": "breaker racked out",
                                                 "left": "run the load test", "unsafe": "panel door open"})
    now = datetime.now(UTC)
    s = handover.facts(conn, now - timedelta(hours=12), now)["sections"]
    [field] = [x for x in s["In the field"] if f"exception:{e}" in x["cites"]]
    assert field["text"].startswith("Look at G-7006 load test, at shift change: fred on site since ")
    assert "handed over to gina, waiting to be accepted (unsafe to leave: panel door open)" in field["text"]
    permits = {x["text"].split()[0]: x["text"] for x in s["Open permits"]}
    assert "PTW-24102" not in permits  # expired
    assert permits["PTW-24101"].startswith("PTW-24101 cold work on ") and \
        "1 of 3 isolations not in place (ISO-24101-3 planned)" in permits["PTW-24101"]
    assert permits["PTW-24103"].endswith("; 1 isolation in place")
    assert all(x["cites"][0].startswith("entity:") for x in s["Open permits"])


def test_okan_spares_come_from_its_own_records(okan_db):
    conn = okan_db.conn
    short = conn.execute(
        "select b.src from edge b join golden s on s.fuzer_id = b.dst and s.field = 'stock' "
        "where b.rel = 'uses_part' and (s.value #>> '{}')::numeric = 0 limit 1").fetchone()["src"]
    out = readiness.check(conn, [short])
    assert out["ready"] is False and out["checks"][0]["checked"]
    ordered = conn.execute("select count(*) as n from entity where kind = 'purchase_order'").fetchone()["n"]
    centers = readiness.crew(conn, [short])["centers"]
    assert centers and {c["label"] for c in centers} <= {"MECH", "INST", "ELEC"} and all(c["crew"] for c in centers)
    held = conn.execute("select c.dst from edge c join entity p on p.fuzer_id = c.src and p.kind = 'permit' "
                        "where c.rel = 'covers' limit 1").fetchone()["dst"]
    assert readiness.permits(conn, [held])["permits"]
    assert ordered > 0

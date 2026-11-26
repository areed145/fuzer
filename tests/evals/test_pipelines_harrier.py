"""The Pipelines & Commercial specialist (P4.7, #1259) on Harrier, from its bundle alone: every case raised on the line,
and a trip of the pump that drives it (P4.7a, #1430), gets this specialist's brief, its tools carry the facts its answer keys expect, what it can't settle is kept for what
would settle it (P6.6), and Okan and Kestrel are as they were."""

import json
from datetime import datetime, timedelta

import pytest

from fuzer.agents import brief, packs
from fuzer.agents.capabilities import pipelines
from fuzer.analysis import trips
from fuzer.core import work
from fuzer.core.pack import current_pack
from fuzer.ops import integrity, pipeline, replan

pytestmark = pytest.mark.sequence
INTEGRITY, OUTAGES = "pipelines-commercial/pipeline-integrity", "pipelines-commercial/outage-planning"
HYDRAULICS = "pipelines-commercial/hydraulics-and-linepack"
LINE = ("pipeline", "pipeline_segment", "pipeline_feature")  # what a case on the line names
BRIEFED = """Briefed.

```json
{"what_happened": "w", "evidence": [{"ref": "r", "says": "s"}], "analysis": [{"method": "m", "says": "s"}],
 "similar": [{"case": "c", "fixed_by": "f"}], "proposal": {"disposition": "d", "confidence": 0.6, "why": "y"},
 "could_not_check": []}
```"""


@pytest.fixture(scope="module")
def c(harrier_db):
    """Harrier with its pig run anomaly and its origin station's pump trip raised, and a planner's ask for windows for
    the mainline outage."""
    integrity.watch_ili(harrier_db)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    trips.pump_scan(harrier_db, current_pack(harrier_db), (today - timedelta(days=2)).isoformat(), today.isoformat(),
                    by="test")
    pipeline.sync_plan(harrier_db)
    work.add_person(harrier_db, "pat", ["maintenance_planner"], "test")
    replan.outage_windows(harrier_db, "HR-PIG-2027", "pat")
    harrier_db.commit()
    return harrier_db


ORIGIN_PUMPS = """select u.src from edge u join edge s on s.dst = u.dst and s.rel = 'starts_at'
join entity l on l.fuzer_id = s.src and l.kind = 'pipeline'
join edge k on k.src = u.src and k.rel = 'classified_as' join entity_label c on c.fuzer_id = k.dst
where u.rel = 'part_of' and c.label like '%:PU%'"""


def on_the_line(conn) -> list[dict]:
    """Every case one of whose subjects is a pipeline, one of its segments or a feature found on it."""
    return [dict(r) for r in conn.execute(
        "select * from work_item i where exists (select 1 from entity e where e.fuzer_id = any(i.subjects) "
        "and e.kind = any(%s)) order by i.id", (list(LINE),))]


def test_every_case_on_the_line_gets_this_specialists_brief(c):
    cases = on_the_line(c)
    by_type = {i["type"]: i for i in cases}
    assert set(by_type) == {"ili_anomaly"}, sorted(by_type)
    for i in cases:
        assert brief.owner(c, i)["path"] == INTEGRITY, (i["type"], i["title"])
    # the origin station's mainline pump tripped: the line's pump, so its trip is this specialist's hydraulics brief
    [pump] = [dict(r) for r in c.execute("select * from work_item where type = 'machine_trip'")]
    assert "A1-P-1003A" in pump["title"] and brief.owner(c, pump)["path"] == HYDRAULICS
    # so is a midline pump's: the midline station is where the segment after it starts
    for tag in ("B1-P-2001A", "B1-P-2001B"):
        fid = c.execute("select fuzer_id from entity_label where label = %s", (tag,)).fetchone()["fuzer_id"]
        assert brief.owner(c, {"type": "machine_trip", "subjects": [fid]})["path"] == HYDRAULICS, tag
    # an outage's windows are a plan change the planner agent works out: no pack briefs a plan change, here or on
    # Okan and Kestrel, so none of theirs waits on one
    window = c.execute("select * from work_item where type = 'plan_change' and title like 'Harrier mainline outage%%'"
                       ).fetchone()
    assert window and brief.owner(c, dict(window)) is None and "plan_change" not in brief.briefed_types()
    asked = []
    out = brief.prepare(c, by_type["ili_anomaly"]["id"],
                        lambda q: asked.append(q) or {"answer": BRIEFED, "usage": {"cost_usd": 0.1}})
    assert out["pack"] == INTEGRITY and out["brief"]["proposal"]["disposition"] == "d"
    assert "Ask the pipelines-commercial specialist to load the `pipeline-integrity` skill" in asked[0]
    out = brief.prepare(c, pump["id"], lambda q: asked.append(q) or {"answer": BRIEFED, "usage": {"cost_usd": 0.1}})
    assert out["pack"] == HYDRAULICS and "load the `hydraulics-and-linepack` skill" in asked[1]
    c.commit()


def test_the_packs_tools_carry_the_facts_their_answer_keys_expect(c):
    """No model: the facts each key must name are in what the pack's own tools return on Harrier."""
    keys = {p: packs.load(p)["questions"]["harrier"] for p in (INTEGRITY, OUTAGES)}
    assert len(keys[INTEGRITY]) == 2 and len(keys[OUTAGES]) == 3
    from fuzer.agents.mcp_server import ili_anomalies

    ili = {a["feature"]: a for x in ili_anomalies("HR-PL-001")["lines"] for a in x["anomalies"]}
    assert ili["ILI-26-0029"]["segment"] == "HR-PL-001-S02" and ili["ILI-26-0029"]["screen"] == "exceeds_within_5_years"
    assert ili["ILI-26-0112"]["screen"] == "no_strength" and "grade" in ili["ILI-26-0112"]["why"]
    [line] = pipelines.pipeline_segments("HR-PL-001")["lines"]
    assert line["starts"] == {"area": "HRPT-A1", "meters": ["A1-FT-1010"]}
    assert line["ends"] == {"area": "HRPT-D1", "meters": ["D1-FT-4002"]}
    seg = {s["label"]: s for s in line["segments"]}
    assert list(seg) == [f"HR-PL-001-S0{n}" for n in range(1, 6)] and seg["HR-PL-001-S02"]["class_location"] == "3"
    assert seg["HR-PL-001-S02"]["to_valve"] == "MLV-102" and seg["HR-PL-001-S05"].get("grade") is None
    assert seg["HR-PL-001-S02"]["features"] >= 1  # the crossing's corrosion, on its heavy wall
    book = {x["key"]: x for x in pipelines.customer_contracts()["contracts"]}
    assert set(book) == {"HR-TSA-101", "HR-TSA-102", "HR-TML-201"}
    got = pipelines.outage_impact(90)
    pig = next(o for o in got["outages"] if o["key"] == "HR-PIG-2027")
    assert {h["contract"] for h in pig["hits"]} == {"HR-TSA-101", "HR-TSA-102"} and pig["usd"] > 0  # not HR-TML-201
    twice = {x["customer"] for x in got["by_customer"] if x["flagged"]}
    assert twice == {"Northgate Crude Marketing", "Prairie Basin Producers"}
    said = json.dumps(got)
    assert "Relief valve bench test" in said and "unknown" not in got


def test_the_hydraulics_tools_carry_the_facts_its_answer_keys_expect(c):
    """No model: the pump that tripped, what tripped it and the meters a line balance reads are in what the hydraulics
    pack's own tools return on Harrier; what needs a hydraulic model its playbook says is unknown."""
    from fuzer.agents.capabilities import studies

    keys = packs.load(HYDRAULICS)["questions"]["harrier"]
    assert len(keys) == 2
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    stops = studies.trip_analysis((today - timedelta(days=2)).isoformat(), today.isoformat(), "HRPT-A1")
    assert "A1-P-1003A" in json.dumps(stops)
    tripped = studies.calc("compressor_trip", "A1-P-1003A", until=today.isoformat())
    assert "vibration" in json.dumps(tripped).lower()
    [line] = pipelines.pipeline_segments("HR-PL-001")["lines"]
    assert line["starts"]["meters"] == ["A1-FT-1010"] and line["ends"]["meters"] == ["D1-FT-4002"]
    assert "hydraulic model" in packs.load(HYDRAULICS)["playbook"]


def test_what_it_cannot_settle_is_kept_for_what_would_settle_it(c):
    """P6.6: what a tool the brief's specialist called says it can't say is kept on the case, with what would settle
    it; the assistant keeps the same on an answer (agents.service)."""
    from fuzer import agents
    from fuzer.ops import undetermined

    line = c.execute("select fuzer_id from entity where kind = 'pipeline'").fetchone()["fuzer_id"]
    with c.transaction():
        item = c.execute("insert into work_item (type, title, subjects, created_by) values ('ili_anomaly', "
                         "'A second anomaly', %s, 'test') returning id", ([line],)).fetchone()["id"]
    said = agents.unknown_in("customer_contracts", dict(pipelines.NO_CONTRACTS))
    assert said["reason"] == "no_data" and said["needs"] == {"kind": "source", "name": "customer contracts"}
    out = brief.prepare(c, item, lambda q: {"answer": BRIEFED, "usage": {"cost_usd": 0.1}, "tools": [
        {"agent": "pipelines-commercial", "tool": "customer_contracts", "undetermined": said}]})
    assert out["pack"] == INTEGRITY
    kept = c.execute("select actor, data from work_event where item_id = %s and kind = %s",
                     (item, undetermined.KIND)).fetchall()
    assert [(r["actor"], r["data"]["needs"]["name"]) for r in kept] == [("agent:pipelines-commercial",
                                                                        "customer contracts")]
    c.commit()


def test_okan_and_kestrel_are_as_they_were(okan_db, kestrel_db, monkeypatch):
    """Neither has a pipeline case or a contract register: no case of theirs goes to this specialist, its packs are
    marked not applicable there with why, and its eval asks them nothing. Asked about contracts there, its tool says
    none are loaded, which the assistant keeps as what it couldn't determine (P6.6)."""
    from fuzer.evals import packs_eval

    for name, conn in (("okan", okan_db.conn), ("kestrel", kestrel_db)):
        for i in conn.execute("select * from work_item").fetchall():
            owner = brief.owner(conn, dict(i))
            assert not owner or owner["owner"] != "pipelines-commercial", (name, i["type"])
        # a pump at a line's origin station (Okan's export pumps) trips as before: no pack briefs a pump's trip there
        for r in conn.execute(ORIGIN_PUMPS).fetchall():
            assert brief.owner(conn, {"type": "machine_trip", "subjects": [r["src"]]}) is None, (name, r["src"])
        conn.rollback()
        res = packs_eval.score(name, [INTEGRITY, OUTAGES, HYDRAULICS], ask=lambda cases, say=None: pytest.fail("asked"))
        assert res["asked"] == 0 and set(res["not_applicable"]) == {INTEGRITY, OUTAGES, HYDRAULICS}
    monkeypatch.setenv("FUZER_DATABASE_URL", okan_db.url)
    assert pipelines.customer_contracts() == pipelines.NO_CONTRACTS
    assert pipelines.outage_impact(90)["note"] == "no customer contracts loaded"

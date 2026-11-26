"""What Fuzer couldn't determine (P6.6, #1258), on Okan with its stock levels missing: readiness says the parts are
unknown and why instead of reading them as none in stock, the Options card carries it as a typed note, choosing an
option keeps it on the case, and How the work is running ranks what would answer it by cases, then value at stake."""

import pytest

from fuzer.ops import exceptions as ex
from fuzer.ops import options, readiness, undetermined

WELLS = ["OK-J01-01", "OK-J01-02", "OK-J01-03", "OK-J01-04"]


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.analysis import trips  # noqa: F401  declares machine_trip
    from fuzer.core import work
    from fuzer.workflow import engine

    c = okan_db.conn
    engine.load_defaults(c)
    work.add_person(c, "sam", ["operator"], "test")
    work.add_person(c, "riley", ["ops_lead", "engineer"], "test")
    c.commit()
    return c


@pytest.fixture
def client(conn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import workflow

    app = FastAPI()
    app.include_router(workflow.router)
    app.dependency_overrides[workflow.get_conn] = lambda: conn
    return TestClient(app)


def _order(conn) -> str:
    """A work order on Okan with parts reserved on it."""
    return conn.execute("select src from edge where rel = 'reserves' and valid_to is null order by src limit 1"
                        ).fetchone()["src"]


def test_a_note_takes_only_the_fixed_reasons_and_kinds():
    n = undetermined.note("Are the parts in stock?", "no_data", "source", "stock levels", "no stock source", "readiness")
    assert n["needs"] == {"kind": "source", "name": "stock levels"}
    with pytest.raises(ValueError, match="reason"):
        undetermined.note("x", "guess", "source", "y", "z", "t")
    with pytest.raises(ValueError, match="needs"):
        undetermined.note("x", "no_data", "person", "y", "z", "t")


def test_a_measure_the_assistant_read_as_unknown_names_the_source_it_needs(okan_db):
    from fuzer.agents import unknown_in
    from fuzer.core import metrics
    from fuzer.core.pack import current_pack

    v = metrics.value(okan_db.conn, current_pack(), "safety.bypasses_active", window="1d")
    assert v["unknown"]  # Okan loads no bypass register
    said = unknown_in("metric_value", v)
    assert said["reason"] == "no_data" and said["needs"] == {"kind": "source", "name": "bypass register"}
    assert said["asked"] == "Safeguards bypassed" and said["source"] == "metric_value"
    assert unknown_in("metric_value", {**v, "unknown": False}) is None and unknown_in("x", "text") is None


def test_with_no_stock_source_parts_are_unknown_and_choosing_keeps_what_would_answer_it(conn):
    order = _order(conn)
    unit = conn.execute("select dst from edge where src = %s and rel = 'concerns' limit 1", (order,)).fetchone()["dst"]
    stocked = readiness.spares(conn, [order])
    assert stocked["checked"] and not stocked.get("unknown")  # Okan loads its stock levels
    conn.execute("delete from golden where field = 'stock'")  # the stock source is missing
    e = ex.raise_(conn, "machine_trip", "undetermined trip", "20-K-2300B tripped: four lifted wells on OKJ-01 down",
                  "agent:sre", refs=[*(f"well:{w}" for w in WELLS), unit, order], priority=0.9, role=None)
    conn.commit()

    spares = readiness.spares(conn, [order])
    assert spares["unknown"] == readiness.NO_STOCK and spares["checked"] is False
    assert spares["note"] == "Parts readiness unknown: no stock source."
    assert spares["blockers"] == [] and all(p["stock"] is None and p["short"] is None for p in spares["parts"])
    ready = readiness.for_exception(conn, e["id"])
    assert ready["unknown"] == ["Parts readiness unknown: no stock source."]
    assert not any(b.startswith("Spares:") for b in ready["blockers"])  # unknown, never "0 in stock"

    out = options.for_exception(conn, e["id"])
    dispatch = next(o for o in out["options"] if o["key"] == "dispatch")
    assert dispatch["unknown"] == ["Parts readiness unknown: no stock source."]
    stock = [n for n in out["undetermined"] if n["needs"]["name"] == "stock levels"]
    assert stock == [{"asked": "Are the parts for this job in stock?", "reason": "no_data",
                      "needs": {"kind": "source", "name": "stock levels"},
                      "detail": "parts readiness unknown: no stock source", "source": "readiness"}]
    assert not any(g["needs"]["name"] == "stock levels" for g in undetermined.gather(conn))  # a view keeps nothing

    chosen = options.choose(conn, e["id"], "riley", "dispatch", to="sam", override="parts checked by phone")
    conn.commit()
    item = chosen["event"]["item_id"]
    kept = conn.execute("select actor, data from work_event where item_id = %s and kind = 'undetermined'",
                        (item,)).fetchall()
    assert any(k["data"]["needs"]["name"] == "stock levels" and k["data"]["value_usd"] for k in kept), kept
    assert undetermined.keep(conn, stock, "system", item_id=item) == 0  # the same question isn't kept twice
    group = next(g for g in undetermined.gather(conn) if g["needs"]["name"] == "stock levels")
    assert group["count"] == 1 and group["reasons"] == {"no_data": 1} and group["value_usd"] > 0
    assert group["cases"][0]["id"] == item and "readiness" in group["sources"]
    conn.rollback()


def test_the_view_ranks_what_would_answer_it_by_cases_then_value_at_stake(conn, client):
    items = [conn.execute("insert into work_item (type, title, created_by) values ('machine_health', %s, 'test') "
                          "returning id", (f"rank case {i}",)).fetchone()["id"] for i in range(4)]
    tag = undetermined.note("Is the seal leaking?", "no_tool", "tool", "seal leak calculator", "no tool for it", "t")
    pid = undetermined.note("What does the drawing show?", "no_skill", "skill", "drawing reading", "no skill", "t")
    big = undetermined.note("What is the flow?", "conflicting_evidence", "source", "flow meter", "two meters differ", "t")
    for i in items[:2]:
        undetermined.keep(conn, [tag], "system", item_id=i, value_usd=100)
    undetermined.keep(conn, [pid], "system", item_id=items[2], value_usd=50)
    undetermined.keep(conn, [pid], "agent:x", answer="answer-1")
    undetermined.keep(conn, [big], "system", item_id=items[3], value_usd=90000)
    conn.commit()
    try:
        r = client.get("/work/undetermined")
        assert r.status_code == 200, r.text
        mine = [g for g in r.json() if g["needs"]["name"] in ("seal leak calculator", "drawing reading", "flow meter")]
        # two cases each first, the one with more at stake ahead; then one case with the most at stake
        assert [g["needs"]["name"] for g in mine] == ["seal leak calculator", "drawing reading", "flow meter"]
        seal, drawing, flow = mine
        assert seal["count"] == 2 and seal["value_usd"] == 200 and {c["id"] for c in seal["cases"]} == set(items[:2])
        assert drawing["count"] == 2 and drawing["answers"] == 1 and drawing["value_usd"] == 50
        assert flow["count"] == 1 and flow["value_usd"] == 90000 and flow["reasons"] == {"conflicting_evidence": 1}
        assert client.get("/work/undetermined", params={"since": "2999-01-01T00:00:00Z"}).json() == []
    finally:
        conn.execute("delete from work_event where kind = 'undetermined' and data ->> 'source' = 't'")
        conn.execute("delete from work_item where id = any(%s)", (items,))
        conn.commit()

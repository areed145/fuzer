"""Human gates as policy (P5.10, #619): confidence gates by decision kind and asset criticality, each decision
recorded, and fixed gates that no agent, webhook, API caller or system can pass."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from fuzer.core import db, gates
from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception
from fuzer.workflow import engine


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        engine.set_person(c, "ines", ["ops_lead", "workflow_admin"])
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment')")
        c.commit()
        yield c


@pytest.fixture(scope="module")
def client(conn):
    from fuzer.api import app

    return TestClient(app)


def case(conn, type: str = "task", **kw) -> int:
    out = engine.create_item(conn, type, f"A {type} case", "ines", **kw)
    conn.commit()
    return out["id"]


# --- confidence gates ----------------------------------------------------------------------------------------------

def test_a_confident_match_goes_ahead_and_says_why(conn):
    out = gates.check(conn, "auto_match", 0.995, actor="agent:steward", ref="match:1")
    assert out["proceed"] and out["threshold"] == 0.99
    assert out["reason"] == "An automatic match went ahead: its confidence 100% meets the 99% needed."
    row = conn.execute("select * from gate_decision where id = %s", (out["decision_id"],)).fetchone()
    assert (row["confidence"], row["threshold"], row["fired"]) == (0.995, 0.99, False)
    conn.commit()


def test_lower_confidence_stops_for_a_person_in_plain_words(conn):
    item = case(conn)
    out = gates.check(conn, "auto_close", 0.82, actor="agent:sre", item_id=item)
    assert not out["proceed"] and out["fired"]
    assert out["reason"] == ("An automatic close as duplicate or false alarm stopped for a person: its confidence 82% "
                             "is below the 95% needed.")
    assert [d["reason"] for d in gates.decisions(conn, item)] == [out["reason"]]
    conn.commit()


def test_an_agent_disposition_and_anything_it_could_not_check_always_stop(conn):
    assert gates.check(conn, "agent_disposition", 0.999, actor="agent:sre")["fired"]
    out = gates.check(conn, "auto_match", 0.999, actor="agent:steward", could_not_check=["the SAP equipment record"])
    assert out["fired"] and "could not check the SAP equipment record" in out["reason"]
    conn.commit()


def test_raising_criticality_tightens_the_threshold_without_a_process_change(conn):
    assert gates.criticality(conn, "fz-pump-1") is None
    assert gates.check(conn, "auto_close", 0.97, actor="agent:sre",
                       criticality=gates.criticality(conn, "fz-pump-1"))["proceed"]
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-pump-1', 'criticality', %s, "
                 "'sap', 'test')", (Jsonb("A"),))
    conn.commit()
    out = gates.check(conn, "auto_close", 0.97, actor="agent:sre", criticality=gates.criticality(conn, "fz-pump-1"))
    assert out["fired"] and out["reason"].endswith("on an A-criticality asset always stops for a person.")
    conn.commit()


def test_the_policy_is_data_a_person_changes_and_a_never_loosens(conn, client):
    r = client.put("/settings/gates/auto_close", json={"by": "ines", "default": 0.9, "by_criticality": {"B": 0.98}})
    assert r.status_code == 200, r.text
    assert gates.threshold(conn, "auto_close", "B") == 0.98 and gates.threshold(conn, "auto_close", None) == 0.9
    assert gates.threshold(conn, "auto_close", "A") is None
    bad = client.put("/settings/gates/auto_close", json={"by": "ines", "default": 0.9, "by_criticality": {"A": 0.5}})
    assert bad.status_code == 400 and "always stop" in bad.json()["detail"]
    assert client.put("/settings/gates/field_dispatch", json={"by": "ines"}).status_code == 400
    assert client.put("/settings/gates/auto_close", json={"by": "agent:sre", "default": 0.5}).status_code == 403
    policy = client.get("/settings/gates").json()
    assert [f["kind"] for f in policy["fixed"]] == list(gates.FIXED)
    client.put("/settings/gates/auto_close", json={"by": "ines", "default": 0.95, "by_criticality": {}})


# --- fixed gates: every path is refused for a machine actor ---------------------------------------------------------

def test_an_agent_cannot_dispatch_to_the_field(conn):
    item = case(conn)
    with pytest.raises(gates.GateRefused, match="Dispatching people to the field needs a named person"):
        engine.dispatch_item(conn, item, "agent:sre", "role:operator")
    assert engine.dispatch_item(conn, item, "ines", "role:operator")["to"] == "role:operator"


def test_the_api_refuses_a_machine_actor_and_lets_a_person_through(conn, client):
    from fuzer.ops import exceptions as ex

    TYPES["gate_test"] = ExceptionType("gate_test", "Gate test", role="role:ops_lead")
    try:
        e = raise_exception(conn, "gate_test", "k1", "Something to check", "sre")
        conn.commit()
        refused = client.post(f"/exceptions/{e['id']}/dispatch", json={"by": "agent:sre", "to": "role:operator"})
        assert refused.status_code == 403 and "named person" in refused.json()["detail"]
        assert ex.dispatch(conn, e["id"], "ines", "role:operator")
        conn.commit()
    finally:
        TYPES.pop("gate_test", None)


def test_a_safety_case_is_closed_by_a_person_only(conn, client):
    item = case(conn, "iow_breach")
    r = client.post(f"/work/items/{item}/transition", json={"by": "agent:sre", "state": "done"})
    assert r.status_code == 403 and "Closing a safety or environmental case" in r.json()["detail"]
    with pytest.raises(gates.GateRefused):
        engine.close_item(conn, item, "done", "system")
    with pytest.raises(gates.GateRefused):
        engine.cancel_item(conn, item, "agent:sre")
    assert conn.execute("select state from work_item where id = %s", (item,)).fetchone()["state"] == "open"
    assert engine.close_item(conn, item, "done", "ines")["state"] == "done"


def test_a_webhook_clearing_a_safety_condition_leaves_its_case_open(conn):
    from fuzer.ops import exceptions as ex

    gates.set_safety_case_types(conn, ["flare_exceedance"], "ines")
    assert "flare_exceedance" in gates.safety_case_types(conn) and "iow_breach" in gates.safety_case_types(conn)
    TYPES["flare_exceedance"] = ExceptionType("flare_exceedance", "Flare exceedance", role="role:ops_lead")
    try:
        e = raise_exception(conn, "flare_exceedance", "FL-1", "Flare above its permit", "flare webhook")
        conn.commit()
        assert gates.machine("flare webhook")
        ex.clear(conn, "flare_exceedance", "FL-1", "flare webhook", reason="flow back under the limit")
        conn.commit()
        item = conn.execute("select state from work_item where id = %s", (e["item_id"],)).fetchone()
        assert item["state"] == "open"
        said = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented'",
                            (e["item_id"],)).fetchone()["t"]
        assert "closed by a person" in said
    finally:
        TYPES.pop("flare_exceedance", None)


def test_a_process_is_published_by_a_person_not_an_agent(conn):
    key = conn.execute("select process_key from work_definition where state = 'published' limit 1").fetchone()[
        "process_key"]
    draft = engine.draft(conn, key, "ines")
    conn.commit()
    with pytest.raises(gates.GateRefused, match="Publishing"):
        engine.publish(conn, draft["id"], "agent:workflow")
    assert conn.execute("select state from work_definition where id = %s", (draft["id"],)).fetchone()["state"] == "draft"


def test_a_detector_parameter_is_changed_by_a_person_not_an_agent(conn):
    from fuzer.core import detectors

    d = next(x for x in detectors.registry().values() if x.params)
    key = next(iter(d.params))
    with pytest.raises(gates.GateRefused, match="Changing a limit or detector parameter"):
        detectors.tune(conn, d.name, key, 1, by="agent:sre")


def test_machine_actors(conn):
    assert all(gates.machine(a) for a in ("agent:sre", "system", "fuzer", "sap webhook", "detector:x", "", None))
    assert not any(gates.machine(a) for a in ("ines", "user:4f2a", "Ana Ruiz"))

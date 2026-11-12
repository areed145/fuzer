"""The loop acceptance eval (P1.7): every type of work on a bundle goes arrive to counted, and a type that can't
says at which step and why."""

from fuzer.evals import loop


def test_every_type_of_work_on_okan_completes_the_loop(okan_db):
    res = loop.run(okan_db.conn)
    done = {r["type"]: r for r in res["types"]}
    assert {"upset", "iow_breach", "sap_notification", "machine_trip", "machine_health", "records_gap",
            "source_issue"} <= set(done), sorted(done)  # every routed type, declared before any was raised
    assert not res["failed"], "\n".join(res["failed"])
    assert all(list(r["steps"]) == list(loop.STEPS) for r in res["types"])
    assert done["upset"]["steps"]["route"] == "started upset" and done["upset"]["steps"]["count"].endswith("(Resolved)")
    assert done["sap_notification"]["steps"]["triage"] == "on the board, to dispatch"
    assert done["source_issue"]["steps"]["field"].startswith("skipped")  # data work stays at the desk


def test_a_type_that_cannot_complete_says_at_which_step(okan_db, monkeypatch):
    monkeypatch.setitem(loop.ARRIVE, "sap_notification", {"equipment": "fz-pump-raw-id"})  # an id the field can't read
    [r] = loop.run(okan_db.conn, only=["sap_notification"])["types"]
    assert r["failed"] == "field" and "raw ids" in r["why"] and list(r["steps"]) == list(loop.STEPS[:5])

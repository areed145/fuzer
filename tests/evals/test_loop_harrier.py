"""Gate C (P8.4): a third bundle of another asset type, the Harrier crude pipeline and terminal, goes through the loop
eval (P1.7) on the standard operating model, stood up from its seed bundle with no change under src/fuzer/. It has
pump stations, a mainline, tanks, ship loading and a vapour recovery unit, and no wells."""

from fuzer.evals import loop
from fuzer.workflow import operating_model as om


def test_every_type_of_work_on_harrier_completes_the_loop(harrier_db):
    adopted = om.adopt(harrier_db, by="test")  # a bundle just stood up: adopt loads the shipped processes itself
    assert adopted["applied"] and adopted["deviations"] == 0 and adopted["unnamed"] == []  # standard v1, as it ships

    res = loop.run(harrier_db)
    assert len(res["types"]) >= 20 and not res["failed"], "\n".join(res["failed"])
    arrived = {r["type"]: r["arrived"] for r in res["types"]}
    done = {r["type"]: r for r in res["types"]}
    # the plant-floor work comes from its own detectors: yesterday's VRU trip, its leaking valve, the tank vapour
    # header past its IOW while the oxidiser took the vapours
    assert arrived["machine_trip"] == "compressor_trips" and arrived["machine_health"] == "machine_health"
    assert arrived["iow_breach"] == "iow_breaches" and arrived["upset"] == "the SRE scan's window detectors"
    assert done["machine_trip"]["steps"]["arrive"].endswith("G1-K-7001")
    # the register build's questions, the deliveries and the drawing revision, as on Okan and Kestrel
    assert arrived["measurement_role_to_confirm"] == arrived["conflicting_sources"] == "the register build"
    assert arrived["drawing_line_to_confirm"] == "a delivery of pid_sheets and the rebuild after it"
    assert arrived["round_finding"] == "an operator's reading on R-G1-02"
    # tank A's high-high level shutdown, still bypassed past its expiry in the bypass register
    assert arrived["bypass_overdue"] == "bypass_overdue" and "E1-T-5001A" in done["bypass_overdue"]["steps"]["arrive"]
    # a named deviation from the standard past its review date: work about the operating model, not an asset (P8.2)
    assert arrived["deviation_review_due"].endswith("deviation_reviews")
    assert done["deviation_review_due"]["steps"]["match"] == "matched the operating model's settings:handover.shift_times"
    assert res["synthetic"] == ["curve_to_confirm"], res["synthetic"]  # no Harrier datasheet has a curve to read
    # no wells, no flare meter, and too few alarmed tags to flood
    assert sorted(res["not_here"]) == ["alarm_flood", "flare_event", "gas_lift_instability",
                                     "well_test_review"], res["not_here"]

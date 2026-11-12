"""The loop acceptance eval (P1.7) on Kestrel, in its own module: Okan's fixture points the synthetic historian at
Okan's process model for the whole module it is used in, and Kestrel's detectors must read Kestrel's."""

from fuzer.evals import loop


def test_every_type_of_work_on_kestrel_completes_the_loop(kestrel_db):
    """The same loop on the second bundle, stood up from its seed bundle as a deployment would be."""
    res = loop.run(kestrel_db)
    assert len(res["types"]) >= 7 and not res["failed"], "\n".join(res["failed"])
    arrived = {r["type"]: r["arrived"] for r in res["types"]}  # from its own detector or source (P1.7c, Gate A)
    assert not {"upset", "iow_breach", "sap_notification", "machine_trip", "machine_health"} & set(res["synthetic"]), \
        arrived
    # the register build's questions reach the stewards' queue as they do in production, and work about a source
    # matches one of the project's sources (#747)
    assert not {"conflicting_sources", "context_gap", "criticality_not_assessed", "measurement_role_to_confirm",
                "unmatched_record", "unmatched_records", "unresolved_links"} & set(res["synthetic"]), arrived
    assert not {"failed_quality_check", "mapping_drift", "mapping_to_approve", "source_issue", "source_quality_drop"
                } & set(res["synthetic"]), arrived  # the bundle's next deliveries (deliveries/) and the rebuild after
    done = {r["type"]: r for r in res["types"]}
    assert done["source_issue"]["steps"]["match"] == "matched the oplogs source"
    # an operator's reading on a round, and a stop in the next downtime delivery nothing else records
    assert arrived["round_finding"] == "an operator's reading on R-B1-03"
    assert arrived["records_gap"] == "a delivery and the rebuild after it, then records_gaps"
    assert done["unmatched_records"]["steps"]["match"].startswith("matched the ") and \
        done["measurement_role_to_confirm"]["steps"]["route"] == "in the data_steward queue"
    # a revised smart P&ID export: an alarm table that disagrees with the DCS, and a line only one side shows
    assert not {"drawing_disagreement", "drawing_line_to_confirm"} & set(res["synthetic"]), arrived
    assert res["synthetic"] == ["curve_to_confirm"], res["synthetic"]  # no Kestrel datasheet has a curve to read

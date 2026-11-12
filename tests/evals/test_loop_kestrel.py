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

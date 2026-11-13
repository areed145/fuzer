"""Flare attribution to its source (P4.2e, #850, folds in #351) on Kestrel: yesterday's compressor trip backs the inlet
separator up, and the flare takes its gas. The relief and vent paths come from the piping into the flare knockout
drum, and the source from which relieved item's pressure rose, never from tag names."""

from fuzer.analysis import flare


def test_the_flare_names_the_item_relieving_into_it_and_its_devices(kestrel_db):
    conn = kestrel_db
    knockout = conn.execute("select asset from measurement_role where role = 'flare_flow'").fetchone()["asset"]
    paths = {(d["relieves_label"], d["device_label"]) for d in flare.paths(conn, knockout)}
    assert paths == {("A1-V-1001", "A1-PSV-1001"), ("A1-V-1001", "A1-PV-1001"), ("B1-K-2001", "B1-PSV-2004")}

    (e,) = [e for e in flare.events(conn, days=3) if e["source"] == "point"]
    assert [x["label"] for x in e["relief"]] == ["A1-V-1001"]
    inlet = e["relief"][0]
    assert inlet["devices"] == ["A1-PSV-1001", "A1-PV-1001"] and inlet["rise_pct"] >= 5 and inlet["role"] == "pressure"

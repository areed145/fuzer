"""The PRONTO rig's labelled days load, and change detection is checked against their faults (#904)."""

from fuzer.evals import pronto


def test_the_slice_is_three_labelled_days_with_eight_fault_onsets_and_its_licence():
    days = pronto.days()
    assert [d["day"] for d in days] == ["Testday2", "Testday3", "Testday4"]
    assert [[f for _, f in d["onsets"]] for d in days] == [
        ["Air blockage", "Air leakage"], ["Air leakage", "Air leakage", "Diverted flow", "Diverted flow"],
        ["Slugging", "Slugging"]]
    assert [[o for o, _ in d["onsets"]] for d in days] == [[6, 160], [15, 90, 155, 285], [59, 121]]
    assert all(len(d["readings"]) == 17 and set(pronto.INPUTS) <= set(d["readings"]) for d in days)
    assert all(len(v) == len(d["labels"]) for d in days for v in d["readings"].values())
    assert (pronto.ROOT / "LICENSE").read_text().startswith("Creative Commons Attribution 4.0 International")


def test_the_operators_flow_moves_are_found_and_explain_the_changes_after_them():
    day3 = next(d for d in pronto.days() if d["day"] == "Testday3")
    mv = pronto.moves(day3)
    assert any(64 <= m <= 66 for m in mv) and any(131 <= m <= 137 for m in mv)
    assert pronto.check(day3)["explained"] > 0


def test_change_detection_finds_most_rig_faults_and_raises_nothing_unexplained_in_normal_running():
    res = pronto.score()
    assert res["faults"] == 8 and res["found"] >= 6, res["missed"]
    assert res["by_fault"]["Air leakage"]["found"] == 3 and res["by_fault"]["Diverted flow"]["found"] == 2
    assert res["false_alarms"] == 0, [d["false_alarms"] for d in res["days"]]
    assert all(f["minutes_to_detect"] <= pronto.LAG for d in res["days"] for f in d["faults"] if f["found"])

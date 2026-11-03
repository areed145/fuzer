"""Recorded eval runs (#134): kept without answers, read back oldest first."""

from fuzer.evals import runs


def test_a_recorded_run_keeps_scores_not_answers(tmp_path):
    res = {"questions": 1, "mentions": 1.0, "cost_usd": 0.5,
           "results": [{"domain": "reliability", "question": "MTBF?", "mentions": 1.0, "answer": "long text",
                        "slowest_tools": [{"tool": "calc"}], "seconds": 10.0}]}
    runs.save(runs.assistant(res, {"model": "m"}), "2026-01-02", tmp_path)
    runs.save(runs.ops({"sre": {"recall": 1.0}, "detector": "x"}, "2024-06-10", "2024-06-17", True), "2026-01-01",
              tmp_path)
    got = runs.load(tmp_path)
    assert [r["kind"] for r in got] == ["ops", "assistant"]
    a = got[1]
    assert a["model"] == "m" and a["date"] == "2026-01-02"
    assert "answer" not in a["results"][0] and "slowest_tools" not in a["results"][0]
    assert got[0]["sre"] == {"recall": 1.0} and "detector" not in got[0]

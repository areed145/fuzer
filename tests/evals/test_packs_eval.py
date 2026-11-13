"""P4.2: each specialist pack's answer keys for a bundle, asked and scored, and which packs pass."""

import json

import pytest

from fuzer.evals import evaluate, packs_eval, runs

pytestmark = pytest.mark.unit

COMPRESSION, PUMPS = "maintenance-integrity/compression", "maintenance-integrity/pumps-and-drivers"
WELLS = "wells-subsurface/wells-and-lift"


def answering(text_for):
    """A stand-in for the assistant: answers each question with text_for(case), scored the real way."""
    def ask(cases, say=None):
        return evaluate.summarise([evaluate.score_answer(c, {
            "answer": text_for(c), "specialists": c.get("specialists", []), "tools": [], "seconds": 1.0,
            "usage": {"cost_usd": 0.01}}) for c in cases])
    return ask


def every_fact(case):
    return " ".join(m if isinstance(m, str) else m[0] for m in case["must_mention"])


def test_a_pack_whose_answers_name_its_facts_and_route_right_passes():
    res = packs_eval.score("okan", [COMPRESSION, PUMPS], ask=answering(every_fact))
    assert res["passed"] == res["asked"] == 2
    assert res["packs"][COMPRESSION]["mentions"] == 1.0 and res["packs"][COMPRESSION]["short"] == []


def test_a_pack_whose_answers_miss_facts_falls_short_and_says_by_how_much():
    res = packs_eval.score("okan", [COMPRESSION], ask=answering(lambda c: "I could not tell."))
    p = res["packs"][COMPRESSION]
    assert not p["passed"] and res["passed"] == 0
    assert p["short"] == ["mentions 0% under 80%"]


def test_a_pack_the_bundle_marks_not_applicable_is_listed_with_why_and_not_asked():
    asked = []
    res = packs_eval.score("harrier", [WELLS, PUMPS],
                           ask=lambda cases, say=None: asked.append(cases) or answering(every_fact)(cases))
    assert res["not_applicable"] == {WELLS: "a pipeline and terminal has no wells"}
    assert list(res["packs"]) == [PUMPS] and len(asked) == 1


def test_an_unknown_pack_is_refused():
    with pytest.raises(ValueError, match="no such pack: nope/nope"):
        packs_eval.score("okan", ["nope/nope"], ask=answering(every_fact))


def test_the_recorded_run_keeps_scores_not_answers_and_shows_in_eval_results(tmp_path, monkeypatch):
    from fuzer import reference

    res = packs_eval.score("okan", [COMPRESSION], ask=answering(every_fact))
    run = runs.packs(res, {"model": "m"})
    assert "answer" not in run["packs"][COMPRESSION]["results"][0]
    path = runs.save(run, day="2026-11-13", runs=tmp_path)
    assert path.name == "2026-11-13-packs-okan.json"
    monkeypatch.setattr(runs, "load", lambda: [json.loads(path.read_text())])
    page = reference.eval_results_md()
    assert "## Specialist packs" in page and f"| 2026-11-13 | okan | `{COMPRESSION}` |" in page

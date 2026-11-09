"""Generated reference pages (#134) match the code: regenerate with `uv run python -m fuzer.reference`."""

import pytest

from fuzer import reference
from fuzer.resources import ROOT


@pytest.mark.parametrize("page", sorted(reference.PAGES))
def test_reference_page_is_current(page):
    assert (ROOT / "docs" / page).read_text() == reference.PAGES[page](), f"docs/{page} is stale"


def test_eval_results_show_a_missed_fact_with_alternatives(monkeypatch):
    # A must_mention item can list alternatives; when an answer misses it, `missing` holds that list.
    from fuzer.evals import runs

    run = {"date": "2026-11-09", "kind": "assistant", "model": "m", "questions": 1, "mentions": 0.5, "cited": 1.0,
           "safe": 1.0, "routed": 1.0, "charted": None, "cost_per_answer_usd": 0.5, "seconds_per_answer": 60,
           "results": [{"domain": "reliability", "question": "Who are our bad actors?", "specialists": ["maintenance"],
                        "mentions": 0.5, "missing": [["MTBF", "mean time between failures"], "20-K-2200B"],
                        "citations": 3, "charts": [], "tool_calls": 4, "seconds": 60, "cost_usd": 0.5}]}
    monkeypatch.setattr(runs, "load", lambda: [run])
    assert "| MTBF or mean time between failures, 20-K-2200B |" in reference.eval_results_md()

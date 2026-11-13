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


def test_a_recorded_watch_run_is_kept_per_window_and_shown(monkeypatch, tmp_path):
    # P3.3: `fuzer eval --watch --record` keeps each window's scores; eval-results shows what the watch missed.
    from fuzer.evals import runs

    res = {"bundle": "okan", "since": "2025-01-13T00:00", "until": "2025-01-15T00:00", "usd": 0.26, "upsets": 4,
           "caught": 3, "uncaught": 1, "proposals": 4, "matched_uncaught": 0, "precision": 0.0, "recall": 0.0,
           "passes": False, "uncaught_examples": ["10-P-1001A head_loss 2025-01-01 00:00"], "notes": ["dropped"]}
    a = runs.save(runs.watch(res, "m"), day="2026-11-13", runs=tmp_path)
    b = runs.save(runs.watch({**res, "since": "2025-08-20T00:00"}, "m"), day="2026-11-13", runs=tmp_path)
    again = runs.save(runs.watch(res, "m"), day="2026-11-13", runs=tmp_path)
    assert len({a, b, again}) == 3 and "notes" not in runs.load(tmp_path)[0]  # a rerun keeps the first
    monkeypatch.setattr(runs, "load", lambda: [runs.watch(res, "m") | {"date": "2026-11-13"}])
    page = reference.eval_results_md()
    assert "| 2026-11-13 | okan | 2025-01-13 to 2025-01-15 | m | 4 (1) | 4 | 0 | 0% | 0% | $0.26 | no |" in page
    assert "10-P-1001A head_loss" in page

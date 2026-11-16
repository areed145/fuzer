"""Recorded eval runs (#134): `fuzer eval --agents --record`, `fuzer eval --packs --record`, `fuzer eval --ops --full --record` and
`fuzer eval --watch --record` and `fuzer pid-score --record` write a
run's scores (no answers) to evals/runs/, and docs/eval-results.md is generated from them, so the baselines
are committed with the code that made them.
"""

import json
from datetime import date
from pathlib import Path

from ..resources import ROOT

RUNS = ROOT / "evals" / "runs"
KEEP = ("domain", "question", "mentions", "missing", "citations", "operating_instructions", "specialists", "routed",
        "charts", "charted", "tool_calls", "seconds", "cost_usd")


def assistant(res: dict, models: dict | None = None) -> dict:
    """The assistant eval's summary and per-question scores, without the answers."""
    return {"kind": "assistant", **(models or {}), **{k: v for k, v in res.items() if k != "results"},
            "results": [{k: r.get(k) for k in KEEP} for r in res["results"]]}


def ops(res: dict, since: str, until: str, live: bool) -> dict:
    return {"kind": "ops", "since": since, "until": until, "live": live,
            **{k: res.get(k) for k in ("sre", "grouping", "diagnosis", "daily_report", "opportunities")}}


def pids(res: dict, model: str | None) -> dict:
    """A P&ID benchmark run (#475): per element and per degradation level, with the reader's model."""
    return {"kind": "pids", "model": model, **{k: res.get(k) for k in ("elements", "by_level", "sheets", "sheets_read")}}


def packs(res: dict, models: dict | None = None) -> dict:
    """A specialist pack eval run (P4.2) on one bundle: each pack's summary and per-question scores, no answers."""
    return {"kind": "packs", **(models or {}), "bundle": res["bundle"], "passed": res["passed"], "asked": res["asked"],
            "not_applicable": res["not_applicable"],
            "packs": {p: {**{k: v for k, v in s.items() if k != "results"},
                          "results": [{k: r.get(k) for k in KEEP} for r in s.get("results") or []]}
                      for p, s in res["packs"].items()}}


WATCH = ("upsets", "caught", "uncaught", "proposals", "matched_uncaught", "matched_caught", "false_proposals", "matched",
         "precision", "recall", "cited", "passes", "uncaught_examples", "false_examples")


def watch(res: dict, model: str | None) -> dict:
    """An agentic watch eval run (P3.3) on one bundle's window, or its fixed set of windows (P3.11b) with each
    window's scores: what it proposed against what no rule caught."""
    out = {"kind": "watch", "model": model, "bundle": res["bundle"], "since": res["since"], "until": res["until"],
           "usd": res.get("usd"), **{k: res.get(k) for k in WATCH}}
    if res.get("windows"):
        out["windows"] = [{k: w.get(k) for k in ("since", "until", "why", "usd", *WATCH)} for w in res["windows"]]
    return out


DRIFTS = ("slow_upsets", "slow_detected", "slow_recall", "slow_missed", "detections", "right", "false_alarms",
          "precision", "false_alarms_per_week", "false_alarm_examples", "passes")


def drifts(res: dict) -> dict:
    """A slow-drift detector replay (P3.11a) on one bundle: the slow upsets it raised, and what it raised falsely."""
    return {"kind": "drifts", "bundle": res["bundle"], "since": res["since"], "until": res["until"],
            **{k: res.get(k) for k in DRIFTS}}


def save(run: dict, day: str | None = None, runs: Path = RUNS) -> Path:
    runs.mkdir(parents=True, exist_ok=True)
    run = {"date": day or date.today().isoformat(), **run}
    window = f"-{run['since'][:10]}" if run["kind"] == "watch" and not run.get("windows") else ""  # or one window
    path = runs / f"{run['date']}-{run['kind']}{'-' + run['bundle'] if run.get('bundle') else ''}{window}.json"
    n = 2
    while run["kind"] == "watch" and path.exists():  # a second watch run on a window the same day keeps the first
        path = path.with_name(f"{path.stem.rsplit('~', 1)[0]}~{n}.json")
        n += 1
    path.write_text(json.dumps(run, indent=2, default=str) + "\n")
    return path


def load(runs: Path = RUNS) -> list[dict]:
    """Every recorded run, oldest first."""
    return [json.loads(p.read_text()) for p in sorted(runs.glob("*.json"))] if runs.is_dir() else []

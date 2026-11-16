"""The slow-drift detector scored on replay on every bundle (P3.11a, #1020): `fuzer eval --drifts`.

Each bundle's `evals/drifts.yaml` gives the stretch to replay (`since`, `until`; relative to today where the
bundle's data is): one that holds its slow upsets. The replay runs `drift.find` a day at a time with the clock
at each day's end, as the daily tier runs it, and scores what it raises against the process model's events
(`replay.score`). Slow kinds of upset the SRE's own replay doesn't score (a compressor valve leaking, a gas turbine's
heat rate creeping, a meter reading off) are upsets here, because they are what a drift detector is for.

The scores, per bundle:

- `slow_recall`: of the slow upsets starting in the stretch, the share it raised.
- `precision`: of what it raised, the share on an upset's own unit in its time.
- `false_alarms_per_week`: raises on nothing the model holds. The bar is the SRE's (`ops_eval.CEILINGS`).

They are kept in `ops_eval` as `peer_drifts` with the bundle, which the Detection page reads.
"""

import json
from datetime import timedelta
from pathlib import Path

import yaml

from ..ops import drift
from ..sources import relative_dates
from . import replay

KEY = "peer_drifts"
SLOW = replay.SLOW
UPSETS = replay.UPSETS | SLOW
MAX_FALSE_PER_WEEK = 5.0  # the SRE's bar for false upsets a week (ops_eval.CEILINGS)


def plan(bundle: Path) -> dict | None:
    """The bundle's drift replay stretch, its dates placed on the calendar; None when it declares none."""
    path = Path(bundle) / "evals" / "drifts.yaml"
    if not path.is_file():
        return None
    out = relative_dates.resolve_all(yaml.safe_load(path.read_text()) or {})
    return {"since": str(out["since"])[:10] + "T00:00", "until": str(out["until"])[:10] + "T00:00"}


def run(conn, pack, bundle: str, data_dir, since: str, until: str, say=None) -> dict:
    """Replay [since, until] a day at a time with the drift detector and score it."""
    res = replay.run(conn, pack, drift.find, since, until, data_dir=data_dir, learn_days=None, step=timedelta(days=1),
                     say=say, upsets=UPSETS)
    res.pop("grouping", None)
    slow = {k: v for k, v in res["by_kind"].items() if k in SLOW}
    events, found = sum(v["events"] for v in slow.values()), sum(v["detected"] for v in slow.values())
    return {**res, "detector": KEY, "bundle": bundle, "slow_upsets": events, "slow_detected": found,
            "slow_recall": round(found / events, 3) if events else None,
            "slow_missed": [m for v in slow.values() for m in v["missed"]],
            "passes": (res["false_alarms_per_week"] or 0) <= MAX_FALSE_PER_WEEK and bool(found)}


def store(conn, res: dict) -> None:
    """Keep a run's scores as `peer_drifts`, with its bundle, for the Detection page."""
    conn.execute("insert into ops_eval (detector, since, until, scores) values (%s, %s, %s, %s)",
                 (KEY, res["since"], res["until"], json.dumps(res, default=str)))
    conn.commit()

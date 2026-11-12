"""Well tests as routed work (P3.1b, #339): a well whose test is overdue, or whose latest test moved its water cut or
gas-oil ratio well past its own recent tests. Everything is read from each well's own test history (the `well test`
measurements), so nothing here knows a field, a well type or a test interval.

- `review(conn, until)`: each well tested in the last year, checked three ways against its own history:
  - `test_overdue`: the latest test is older than twice the well's usual interval (its median gap between tests),
    and at least `MIN_OVERDUE_DAYS` old. Everything built on tests (allocation, decline, deferment) leans on a stale one.
  - `water_cut_shift`: the latest water cut is `WC_POINTS` points or more off the median of the `BASE` tests before it.
  - `gor_shift`: the gas-oil ratio on the last two tests is `GOR_RATIO` times or more off, the same way, the median
    of the (up to) `GOR_BASE` tests before them, and further off than `SCATTER` times its own scatter over the earlier tests (a
    GOR read on a test separator is noisy, so one test is never a shift).
  The reservoir mechanism behind a shift (breakthrough, coning, a gas cap) stays a question for the engineer.
- The `well_tests` detector raises each flagged well as `well_test_review` for the production engineer, with the
  reasons and the numbers, and clears it when a newer test no longer flags it.
"""

import math
from datetime import datetime, timedelta
from itertools import pairwise
from statistics import median

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

BASE = 3  # the tests before the latest that it is compared with
WC_POINTS = 10.0  # percentage points of water cut
GOR_RATIO = 1.5  # times, either way
GOR_BASE = 6  # a noisy GOR is compared with the median of up to this many tests before
SCATTER = 4.0  # and that many times the GOR's own test-to-test scatter (median absolute deviation of its log)
OVERDUE = 2.0  # times the well's usual interval
MIN_OVERDUE_DAYS = 60
IN_SERVICE_DAYS = 365  # tested within this long: a well still on the test schedule
WORDS = {"test_overdue": "test overdue", "water_cut_shift": "water cut shift", "gor_shift": "GOR shift"}


def _tests(conn, until: datetime) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        select m.fuzer_id as well, coalesce(l.label, m.fuzer_id) as label, m.record, m.at, m.quantity, m.value
        from measurement m join entity e on e.fuzer_id = m.fuzer_id and e.kind = 'well' and e.retired_at is null
        left join lateral (select label from entity_label where fuzer_id = m.fuzer_id order by label limit 1) l on true
        where m.method = 'well test' and m.at < %s and m.quantity in ('water_cut', 'gor', 'oil_rate', 'gas_rate')
        order by m.fuzer_id, m.at
        """, (until,)).fetchall()
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        t = by.setdefault(r["well"], {}).setdefault(r["record"], {"at": r["at"], "record": r["record"], "label": r["label"]})
        t[r["quantity"]] = r["value"]
    return {w: sorted(ts.values(), key=lambda t: t["at"]) for w, ts in by.items()}


def _check(tests: list[dict], until: datetime) -> dict | None:
    last = tests[-1]
    if (until - last["at"]).days > IN_SERVICE_DAYS:
        return None  # off the test schedule: suspended or abandoned, not overdue
    flags, said = [], {}
    gaps = [(b["at"] - a["at"]).days for a, b in pairwise(tests) if (b["at"] - a["at"]).days > 0]
    usual = median(gaps) if gaps else None
    age = (until - last["at"]).days
    if usual and age >= max(OVERDUE * usual, MIN_OVERDUE_DAYS):
        flags.append("test_overdue")
        said.update(usual_days=round(usual), age_days=age)
    before = [t for t in tests[-1 - BASE:-1]]
    if len(before) == BASE:
        wcs = [t["water_cut"] for t in before if t.get("water_cut") is not None]
        if last.get("water_cut") is not None and len(wcs) == BASE:
            ref = median(wcs)
            said.update(water_cut=last["water_cut"], water_cut_before=ref)
            if abs(last["water_cut"] - ref) >= WC_POINTS:
                flags.append("water_cut_shift")
        base = tests[-2 - GOR_BASE:-2]  # a GOR shift holds on the last two tests, against the tests before both
        gors = [t["gor"] for t in base if t.get("gor")]
        pair = [t.get("gor") for t in tests[-2:]]
        if len(gors) >= BASE and all(pair):
            ref = median(gors)
            said.update(gor=last["gor"], gor_before=ref)
            logs = [math.log(t["gor"]) for t in tests[:-2] if t.get("gor") and t["gor"] > 0]
            mid = median(logs) if logs else 0.0
            mad = median(abs(x - mid) for x in logs) if len(logs) >= 2 * BASE else math.inf
            offs = [math.log(g / ref) for g in pair] if ref > 0 else [0.0, 0.0]
            if (offs[0] * offs[1] > 0
                    and min(abs(o) for o in offs) >= max(math.log(GOR_RATIO), SCATTER * mad)):
                flags.append("gor_shift")
    if not flags:
        return None
    return {"flags": flags, "last_test": last["at"].isoformat(timespec="minutes"), "record": last["record"], **said}


def review(conn, until: str | None = None) -> list[dict]:
    """The wells whose tests need a look at `until` (default the end of the data's today), worst first."""
    from ..graph import operations

    end = datetime.fromisoformat(until) if until else datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1)
    out = []
    for well, tests in _tests(conn, end).items():
        if tests and (c := _check(tests, end)):
            out.append({"well": well, "label": tests[-1]["label"], "tests": len(tests), **c})
    return sorted(out, key=lambda r: (-len(r["flags"]), r["label"]))


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("well_test_review") or exception_type(
        "well_test_review", "Well test to review", role="role:production_engineer", audience=("ioc",), joins=False,
        description="A well whose test is overdue against its own interval, or whose latest test moved its water cut "
                    "or gas-oil ratio well past its recent tests. A question for the production engineer: retest, or "
                    "say what changed. It clears when a newer test no longer flags it.")


REVIEW_TYPE = _type().name


@detectors.register("well_tests", title="A well test overdue, or a water cut or GOR shift on the latest test",
                    kind=detectors.RULE, cadence="daily", raises=(REVIEW_TYPE,), classes=("well",),
                    needs={"well tests": "select 1 from measurement where method = 'well test' limit 1"},
                    eval="test:tests/analysis/test_well_tests.py::"
                         "test_a_well_whose_water_cut_jumps_is_raised_until_a_newer_test_settles_it",
                    found=lambda out: len(out["raised"]))
def watch(conn, until: str | None = None, by: str = "agent:well_tests") -> dict:
    """Each well flagged by `review` raised as `well_test_review` for the production engineer (keyed by the well);
    open ones a newer test no longer flags are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (REVIEW_TYPE,))]
    found_rows = review(conn, until)
    raised, errors = [], []
    for r in found_rows:
        words = ", ".join(WORDS[f] for f in r["flags"])
        found = Finding(
            "well_tests.review", DETERMINISTIC, method="Each well's latest test against its own test history",
            start=r["last_test"], data={"flags": r["flags"], "record": r["record"]},
            items=(fact("Well", r["label"]), fact("Flagged for", words), at("Latest test", r["last_test"]),
                   fact("Days since it", r.get("age_days")), fact("Usual interval, days", r.get("usual_days")),
                   fact("Water cut, %", r.get("water_cut")),
                   fact(f"Water cut over the {BASE} tests before, median %", r.get("water_cut_before")),
                   fact("GOR, Sm3/Sm3", r.get("gor")),
                   fact("GOR over the tests before the last two, median", r.get("gor_before")),
                   fact("Note", "A pattern to look at, not a reservoir diagnosis.")))
        try:
            x = raise_exception(conn, REVIEW_TYPE, r["label"], f"{r['label']}: {words}", by, source="well_tests",
                                refs=[r["well"]], priority=0.6 if "test_overdue" not in r["flags"] else 0.4,
                                finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{r['label']}: {e}")
            continue
        raised.append({"well": r["label"], "flags": r["flags"], "exception_id": x["id"], "item_id": x["item_id"],
                       "new": x["raised"]})
    cleared = []
    for key in set(held) - {r["label"] for r in found_rows}:
        clear_exception(conn, REVIEW_TYPE, key, by, reason="a newer test no longer flags it")
        cleared.append(key)
    conn.commit()
    return {"wells": len(found_rows), "raised": raised, "cleared": cleared, "errors": errors}

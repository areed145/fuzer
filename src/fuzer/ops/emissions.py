"""Work that the emissions sources bring (P3.7, #936): a source pack that carries leak surveys raises the leaks whose
repairs don't hold, with no code on the asset.

A component found leaking in two or more surveys in a row (`ldar.REPEAT_SURVEYS`, a Method 21 reading of
`ldar.LEAK_PPM` ppm or more each time), and still leaking at its latest screening, is raised by the `repeat_leaks`
detector as work for the engineer, keyed by the tag and the component. A leak found once and clean at the next survey
was repaired, which is the programme working, so it raises nothing. The work clears when the component's latest
screening is under the leak threshold. Evidence only, with survey dates: a reading holds for the day it was taken.

A daily gas balance (`gas_balance.daily`: gas handled against export, lift gas, flare, fuel and vent) whose closure gap
has stayed past `gas_balance.GAP_PCT` for `GAP_DAYS` days running, up to the latest report, is raised by the
`gas_balance_gaps` detector as work for the engineer: a meter, or a term the report doesn't carry, is off. It is keyed
by the day the gap opened, and clears when a day closes again. A balance the report can't close (a term it never
carries) raises nothing, since its gap is the missing term and not a fault.
"""

from datetime import date, datetime, timedelta

from ..analysis import gas_balance, ldar
from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

WINDOW_DAYS = 730  # the surveys read back from `now` for consecutive leaks


def _repeat_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("repeat_leak") or exception_type(
        "repeat_leak", "Leak repair not holding", role="role:engineer", audience=("ioc",), joins=False,
        description="A component found leaking in consecutive LDAR surveys and still leaking at its latest screening. "
                    "Each repair has not held, so the leak is likely a worn part rather than a loose fitting. Find the "
                    "cause and plan a lasting repair. It clears when the component screens under the leak threshold.")


REPEAT_TYPE = _repeat_type().name


def _day(now) -> date | None:
    if now is None:
        return None
    return (datetime.fromisoformat(now) if isinstance(now, str) else now).date()


def still_leaking(conn, now=None) -> list[dict]:
    """The repeat leakers (`ldar.surveys` over the `WINDOW_DAYS` to `now`) whose latest screening is still a leak."""
    end = _day(now)
    until = (end + timedelta(days=1)).isoformat() if end else None
    since = (end - timedelta(days=WINDOW_DAYS)).isoformat() if end else None
    out = ldar.surveys(conn, since, until, days=WINDOW_DAYS)
    rows = []
    for r in out["repeat_leakers"]:
        if r["consecutive"] < ldar.REPEAT_SURVEYS:
            continue
        last = conn.execute("select value, at from measurement where fuzer_id = %s and quantity = %s and "
                            "coalesce(location, '') = %s and at < %s order by at desc limit 1",
                            (r["id"], ldar.QUANTITY, r["component"] or "", out["until"])).fetchone()
        if last and float(last["value"]) >= ldar.LEAK_PPM:
            rows.append({**r, "last_ppm": float(last["value"]), "last_screened": last["at"].date().isoformat()})
    return rows


def _surveys(conn, pack) -> bool:
    return conn.execute("select 1 from measurement where quantity = %s limit 1", (ldar.QUANTITY,)).fetchone() is not None


@detectors.register("repeat_leaks", title="A leak repair that does not hold: leaking in consecutive LDAR surveys",
                    kind=detectors.RULE, cadence="daily", raises=(REPEAT_TYPE,),
                    needs={"LDAR survey readings": _surveys},
                    eval="test:tests/ops/test_emissions.py::test_a_leak_found_in_consecutive_surveys_is_work_until_it_screens_clean",
                    found=lambda out: len(out["raised"]))
def watch_repeat_leaks(conn, by: str = "agent:emissions", now=None) -> dict:
    """Each component still leaking after consecutive surveys raised as `repeat_leak` for the engineer (keyed by tag
    and component); open ones now screening clean are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (REPEAT_TYPE,))]
    leaking = still_leaking(conn, now)
    raised, cleared, errors, keys = [], [], [], set()
    for r in leaking:
        key = f"{r['tag']} {r['component']}"
        keys.add(key)
        found = Finding(
            "emissions.repeat_leak", DETERMINISTIC,
            method=f"LDAR survey readings (Method 21): {ldar.LEAK_PPM} ppm or more in {ldar.REPEAT_SURVEYS} or more "
                   "surveys in a row, and at the latest screening",
            start=r["surveys"][-r["consecutive"]], data={"tag": r["tag"], "component": r["component"],
                                                         "surveys": r["surveys"], "max_ppm": r["max_ppm"]},
            items=(fact("Component", f"{r['tag']}: {r['component']}"),
                   fact("Leaking in", r["consecutive"], "surveys in a row"),
                   fact("Surveys that found it", ", ".join(r["surveys"])),
                   fact("Highest reading", r["max_ppm"], "ppm"), fact("Latest reading", r["last_ppm"], "ppm"),
                   at("Latest screening", r["last_screened"]), fact("Leak threshold", ldar.LEAK_PPM, "ppm")))
        title = f"{r['tag']} {r['component']}: leaking in {r['consecutive']} surveys in a row"
        try:
            x = raise_exception(conn, REPEAT_TYPE, key, title, by, source="ldar", refs=[r["id"]], priority=0.6,
                                finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
            continue
        raised.append({"leak": key, "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - keys:
        clear_exception(conn, REPEAT_TYPE, key, by, reason="the component screens under the leak threshold")
        cleared.append(key)
    conn.commit()
    return {"leaking": len(leaking), "raised": raised, "cleared": cleared, "errors": errors}


# ---- the gas balance (P3.7b) -------------------------------------------------------------------------------------

GAP_DAYS = 3  # days running past the threshold before the gap is work: a day or two is a late or corrected report


def _gap_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("gas_balance_gap") or exception_type(
        "gas_balance_gap", "Gas balance not closing", role="role:engineer", audience=("ioc",), joins=False,
        description="The daily gas balance (gas handled against export, lift gas, flare, fuel and vent) has stayed open "
                    "past its threshold for days running. A meter is likely off: a term reading zero while the gap opens "
                    "points at that meter. Check and correct the meter, and the reported volumes. It clears when the "
                    "balance closes again.")


GAP_TYPE = _gap_type().name


def open_gap(conn, now=None) -> dict | None:
    """The balance's open stretch reaching the latest report up to `now`, when it is `GAP_DAYS` or longer and the
    report carries every term; else None."""
    end = _day(now)
    out = gas_balance.daily(conn, None, (end + timedelta(days=1)).isoformat() if end else None, days=60)
    if out["missing_terms"] or not out["episodes"]:
        return None
    last = out["episodes"][-1]
    said = [d for d in out["days"] if d["gap_pct"] is not None]
    if not said or last["end"] != said[-1]["date"] or last["days"] < GAP_DAYS:
        return None
    return {**last, "days_detail": [d for d in said if d["date"] >= last["start"]], "threshold_pct": out["threshold_pct"]}


def _reports(conn, pack) -> bool:
    return conn.execute("select 1 from golden where field = 'fuel_gas_ksm3' limit 1").fetchone() is not None


@detectors.register("gas_balance_gaps", title="The daily gas balance staying open for days: a meter likely off",
                    kind=detectors.RULE, cadence="daily", raises=(GAP_TYPE,),
                    needs={"fuel gas on the daily report": _reports},
                    eval="test:tests/ops/test_emissions.py::test_a_gas_balance_open_for_days_is_work_until_it_closes",
                    found=lambda out: len(out["raised"]))
def watch_gas_balance(conn, by: str = "agent:emissions", now=None) -> dict:
    """The gas balance open for `GAP_DAYS` days running to the latest report raised as `gas_balance_gap` for the
    engineer (keyed by the day it opened); an open one is cleared once a day closes again."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (GAP_TYPE,))]
    gap = open_gap(conn, now)
    raised, cleared, errors, keys = [], [], [], set()
    if gap:
        key = f"gas balance · {gap['start']}"
        keys.add(key)
        zero = ", ".join(gap["terms_reading_zero"]) or "none"
        found = Finding(
            "emissions.gas_balance_gap", DETERMINISTIC,
            method=f"Daily report: gas handled less export, lift gas, flare, fuel and vent, open past "
                   f"{gap['threshold_pct']:g} % of the gas handled for {GAP_DAYS} days or more",
            start=gap["start"], data={"start": gap["start"], "end": gap["end"], "days": gap["days"],
                                      "median_gap_pct": gap["median_gap_pct"], "terms_reading_zero": gap["terms_reading_zero"]},
            items=(at("Open since", gap["start"]), at("Latest report", gap["end"]), fact("Days open", gap["days"]),
                   fact("Median gap", gap["median_gap_pct"], "%"), fact("Gas unaccounted", gap["gap_ksm3"], "kSm3"),
                   fact("Direction", gap["direction"].replace("_", " ")), fact("Terms reading zero", zero),
                   fact("Threshold", gap["threshold_pct"], "% of gas handled")))
        title = f"Gas balance open {gap['days']} days since {gap['start']}" + (
            f": {zero} reading zero" if gap["terms_reading_zero"] else "")
        try:
            days = [r["fuzer_id"] for r in conn.execute(  # the daily reports it rests on: the first and the latest
                "select g.fuzer_id from golden g join entity e on e.fuzer_id = g.fuzer_id and e.kind = 'production_day' "
                "where g.field = 'report_date' and left(g.value #>> '{}', 10) = any(%s) and e.retired_at is null",
                ([gap["start"], gap["end"]],))]
            x = raise_exception(conn, GAP_TYPE, key, title, by, source="gas_balance", refs=days, priority=0.5,
                                finding=found)
            raised.append({"gap": key, "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{key}: {e}")
    for key in set(held) - keys:
        clear_exception(conn, GAP_TYPE, key, by, reason="the gas balance closes again")
        cleared.append(key)
    conn.commit()
    return {"open": bool(gap), "raised": raised, "cleared": cleared, "errors": errors}

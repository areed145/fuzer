"""The full operations eval (#103): everything that runs on a schedule, scored against the fake plant's
ground truth, so a detector, prompt or calc change that makes it worse fails the check.

- SRE: the replay (#103a) of a period with the SRE's detectors. It scores recall, precision, false alarms
  a week and time to detect per kind; grouping (groups per trip, first cause, false upsets); and seconds
  per window.
- Diagnosis: the period's upsets handed off (#96c). The diagnosis must name a unit on the matching injected
  event's own units (or the event's unit) as its likeliest cause, and cite records. Claude's cost per
  upset is recorded.
- Daily report: each day of the period closed (#98). Every line cited and every number in the records;
  a day with a trip leads with it; no operating instructions.
- Opportunity scan: the gas lift reallocation gains (#102) recomputed by hand from the wells' curves.

`ask=None` uses a scripted Claude (no network): it names the upset's first-cause unit, which tests the
wiring and scoring. A live run (`live=True`) uses the real one, and its cost is reported.
"""

import re
import time
from datetime import datetime, timedelta

INSTRUCTION = re.compile(r"\b(increase|decrease|reduce|raise|lower|open|close|set|adjust|start|stop|shut)\s+(the\s+)?"
                         r"\w+.*\bto\s+\d", re.IGNORECASE)  # "set the choke to 40%": an operating instruction


def scripted_ask(question: str) -> dict:
    """A fake diagnosis for CI: names the first cause the question gives, cites nothing it can't."""
    m = re.search(r"Likely first cause: (\S+) on (\S+) \(([^)]+)\)", question)
    unit = m.group(3) if m else "unknown"
    answer = (f"The upset most likely started at {unit}.\n\n```json\n"
              f'{{"causes": [{{"cause": "Upset starting at {unit}", "confidence": 0.6, "evidence": [], '
              f'"confirm_by": "check {unit}"}}], "actions": [{{"action": "Inspect {unit}", "source": ""}}]}}\n```')
    return {"answer": answer, "usage": {"cost_usd": 0.0}}


def run(conn, pack, data_dir, since: str, until: str, live: bool = False, max_diagnoses: int = 5, say=None) -> dict:
    """Score the scheduled jobs over [since, until] on the fake plant in `data_dir`. `say(msg)` gets progress
    lines."""
    from ..ops import daily_report, opportunities, sre, sre_handoff
    from ..sources import process_model
    from ..workflow import engine
    from . import replay

    spec = process_model.read(data_dir)
    say = say or (lambda msg: None)
    t0 = time.perf_counter()
    say(f"ops eval {since} to {until}: 1/4 SRE replay")
    rep = replay.run(conn, pack, "sre", since, until, data_dir=data_dir, say=say)
    out = {"since": since, "until": until, "sre": {k: rep[k] for k in (
        "upsets", "detected", "recall", "precision", "false_alarms_per_week", "seconds_per_window", "by_kind")},
        "grouping": rep.get("grouping")}
    if not rep["upsets"]:
        say("warning: no injected upsets in this window, so recall, grouping and diagnosis can't be scored; "
            "pick a window with upsets (pass --since and --until)")

    # diagnosis: hand off the period's upsets (scan stores them) and check the cause named
    say("2/4 diagnosis: scanning the period" + (" (live Claude)" if live else ""))
    engine.load_defaults(conn)
    sre.scan(conn, pack, since, until, max_days=400)
    ask = None if live else scripted_ask
    h = sre_handoff.handoff(conn, pack, ask=ask, max_diagnoses=max_diagnoses, say=say)
    events = replay.truth(spec, datetime.fromisoformat(since), datetime.fromisoformat(until))
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute("select fuzer_id, label from entity_label")}
    right = scored = cited = 0
    for item in h["items"]:
        if not item.get("diagnosed"):
            continue
        u = conn.execute("select * from upset where id = %s", (item["upset"],)).fetchone()
        d = u["diagnosis"] or {}
        start = u["start"]
        match = [e for e in events if e["upset"] and e["start"] - timedelta(hours=1) <= start <= e["end"] + timedelta(hours=6)
                 and any(labels.get(x) in e["own"] or labels.get(x) == e["unit"] for x in u["units"])]
        if not match or not d.get("causes"):
            continue
        scored += 1
        top = max(d["causes"], key=lambda c: c.get("confidence") or 0)
        names = {n for e in match for n in e["own"] | {e["unit"]}}
        right += any(n in top["cause"] for n in names)
        cited += bool(top.get("cited"))
    out["diagnosis"] = {"upsets_handed_off": h["handed_off"], "diagnosed": h["diagnosed"], "scored": scored,
                        "first_cause_named": round(right / scored, 3) if scored else None,
                        "cited": round(cited / scored, 3) if scored else None, "claude_usd": h["claude_usd"],
                        "client": "live" if live else "scripted"}

    # daily reports over the period
    say("3/4 daily reports")
    days, ok, trip_led, instructions = 0, 0, [], 0
    trip_days = {e["start"].date().isoformat() for e in events if e["kind"] == "trip"}
    d0 = datetime.fromisoformat(since).date()
    while d0 < datetime.fromisoformat(until).date():
        day = d0.isoformat()
        d0 += timedelta(days=1)
        try:
            daily_report.close_day(conn, pack, day)
        except ValueError:
            continue
        r = daily_report.get(conn, day)
        days += 1
        say(f"report {day}: {'ok' if r['checks']['ok'] else 'checks failed'}")
        ok += bool(r["checks"]["ok"])
        instructions += bool(INSTRUCTION.search(r["markdown"]))
        if day in trip_days:
            head = r["markdown"].split("## 2.")[0]
            trip_led.append("Biggest loss" in head and "trip" in head.lower())
    out["daily_report"] = {"days": days, "all_checks_ok": round(ok / days, 3) if days else None,
                           "trip_days_led_by_the_trip": round(sum(trip_led) / len(trip_led), 3) if trip_led else None,
                           "with_operating_instructions": instructions}

    # opportunity gains against a hand calculation from the wells' curves
    from ..analysis import calcs
    from ..core import settings

    say("4/4 opportunity scan")
    errs, breakeven = [], settings.get(conn, "opportunities.gas_lift_breakeven")
    for o in opportunities.gas_lift_moves(conn, until):
        hand = 0.0
        for mv in o["moves"]:
            a = calcs.gas_lift_fit(conn, mv["from_id"], breakeven, until)
            b = calcs.gas_lift_fit(conn, mv["to_id"], breakeven, until)
            hand += mv["ksm3d"] * (b["marginal"] - a["marginal"])
        errs.append(abs(hand - o["gain_sm3d"]) / max(abs(hand), 1e-9))
    out["opportunities"] = {"gas_lift_reallocations": len(errs),
                            "worst_gain_error": round(max(errs), 4) if errs else None}
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


MIN_SCORED = 3  # diagnoses matched to an injected upset; with fewer, first_cause_named isn't gated

GATES = {  # the floor each score must keep; a run under it fails the check
    ("sre", "recall"): 0.9,
    ("grouping", "first_cause_right"): 0.9,
    ("daily_report", "all_checks_ok"): 1.0,
    ("daily_report", "trip_days_led_by_the_trip"): 1.0,
    ("diagnosis", "first_cause_named"): 0.5,
}
# single detections run near 40 a week, most of them simultaneous steps from one cause; the ceiling is on the
# upsets they group into
CEILINGS = {("grouping", "false_upsets_per_week"): 5.0, ("daily_report", "with_operating_instructions"): 0,
            ("opportunities", "worst_gain_error"): 0.02}


def gate(res: dict) -> list[str]:
    """Scores under their floor or over their ceiling."""
    bad = []
    for (a, b), floor in GATES.items():
        v = (res.get(a) or {}).get(b)
        if (a, b) == ("diagnosis", "first_cause_named") and (res.get(a) or {}).get("scored", 0) < MIN_SCORED:
            continue
        if v is not None and v < floor:
            bad.append(f"{a}.{b} {v} < {floor}")
    for (a, b), cap in CEILINGS.items():
        v = (res.get(a) or {}).get(b)
        if v is not None and v > cap:
            bad.append(f"{a}.{b} {v} > {cap}")
    return bad

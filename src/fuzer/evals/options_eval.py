"""Options weighed the same way on every case (P4.8, #1260), scored over a set of events, with no model call.

Each event's options come from `ops.options.for_exception`, the one source the shared skill
`shared/options-and-tradeoffs` tells every specialist and the orchestrator to answer from. The run checks the
yardstick, not the plant:

    same_fields      the share of cases whose open options all carry the YARDSTICK fields, and whose closed ones say
                     why they are closed
    reasoned         the share with one open option recommended, a reason and the factor that decided it (FACTORS)
    unknowns_named   the share where what is not on record is named with its reason: no criticality, nothing it
                     defers that anyone measured

    answer(text, offered)   a specialist's answer on a case read against the options: does it recommend the same
                            option, or name another beside the recommended one and say why (a deciding factor), and
                            does it name each unknown

A run passes when every share is 1.0: a case read on another scale is the inconsistency the skill exists to stop.
"""

import re

from ..ops import options

# Words that name each deciding factor in a specialist's answer.
SAYS = {
    "deferred": re.compile(r"(?i)\bdefer|\bbbl\b|\$|production|separate trip"),
    "risk": re.compile(r"(?i)\brisk|critical|hazard"),
    "readiness": re.compile(r"(?i)\bready|block|permit|crew|parts|spares|clash|already there|on site"),
}


def case(out: dict) -> dict:
    """One event's options against the yardstick."""
    open_ = [o for o in out["options"] if o.get("available")]
    closed = [o for o in out["options"] if not o.get("available")]
    missing = sorted({f for o in open_ for f in options.YARDSTICK if f not in o})
    rec = next((o for o in open_ if o["key"] == out.get("recommended")), None)
    unknown = " ".join(out.get("unknown") or [])
    gaps = []
    if out.get("criticality") is None and "criticality" not in unknown:
        gaps.append("criticality")
    if out.get("loss") is None and out.get("if_it_trips") is None and "what it defers" not in unknown:
        gaps.append("what it defers")
    return {
        "exception_id": out["exception_id"], "type": out["type"],
        "same_fields": not missing and all(o.get("unavailable") for o in closed), "missing": missing,
        "reasoned": bool(rec and (out.get("reason") or "").strip() and out.get("deciding") in options.FACTORS),
        "recommended": out.get("recommended"), "deciding": out.get("deciding"),
        "unknowns_named": not gaps, "unnamed": gaps, "unknown": out.get("unknown") or [],
    }


def run(conn, exception_ids: list[int]) -> dict:
    """The yardstick over each event options apply to; events they don't apply to are listed with why not."""
    cases, skipped = [], []
    for eid in exception_ids:
        out = options.for_exception(conn, eid)
        if not out["applies"]:
            skipped.append({"exception_id": eid, "why_not": out["why_not"]})
            continue
        cases.append(case(out))
    n = len(cases)
    share = (lambda k: round(sum(c[k] for c in cases) / n, 2)) if n else (lambda k: None)
    return {"cases": n, "same_fields": share("same_fields"), "reasoned": share("reasoned"),
            "unknowns_named": share("unknowns_named"), "fields": list(options.YARDSTICK), "factors": list(options.FACTORS),
            "by_case": cases, "skipped": skipped}


def gate(res: dict) -> list[str]:
    """What fell short of the bar, empty when the run passes."""
    if not res["cases"]:
        return ["no cases scored"]
    return [f"{k} {res[k]} < 1.0" for k in ("same_fields", "reasoned", "unknowns_named") if res[k] < 1.0]


def answer(text: str, offered: dict) -> dict:
    """A specialist's answer on a case, read against the options it was given. The skill has it lead with the option
    it recommends: the first option it names is its choice. It agrees when that is the recommended option, or says why
    not when it also names the recommended one and a deciding factor; and it names each unknown (by the words before
    its colon)."""
    said = (text or "").lower()
    open_ = [o for o in offered["options"] if o.get("available")]
    at = {o["key"]: said.find(o["label"].lower()) for o in open_ if o["label"].lower() in said}
    chose = min(at, key=at.get) if at else None
    factors = [k for k, words in SAYS.items() if words.search(text or "")]
    same = chose == offered["recommended"]
    why = bool(chose and not same and offered["recommended"] in at and factors)
    topics = [u.split(":", 1)[0].strip().lower() for u in offered.get("unknown") or []]
    unnamed = [t for t in topics if t not in said]
    return {"chose": chose, "same": same, "says_why": why, "agrees_or_says_why": same or why, "factors": factors,
            "unknowns_named": not unnamed, "unnamed": unnamed}

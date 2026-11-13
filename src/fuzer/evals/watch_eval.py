"""The agentic watch scored in shadow on replay (P3.3b, #931): does what it proposes match the bundle's injected events
that no rule caught?

- The SRE's scheduled scan runs over the period first, so the watch sees the detections the rules made (and leaves
  them alone), as it would on the day.
- The watch then reads each window (a day by default) with the model given: a provider's, or a scripted one in CI.
- Truth is the bundle's process model (`replay.truth`): the upsets overlapping the period. An upset is **caught** when
  a stored detection is on one of its own units within it (an hour before to six hours after, as the replay scores),
  or an exception was raised on one of them within it. The rest are **uncaught**: what the watch is for.
- A proposal **matches** an upset when what it names or cites (a point, by id or name, or a unit) is on one of the
  upset's own units and its time overlaps the upset's, with the same lead and tail.

The scores, per bundle:

- `matched`: the share of proposals that match an uncaught upset. The epic's bar is at least half.
- `precision`: the share of proposals that match any upset, caught or not.
- `recall`: the share of uncaught upsets some proposal matched.
- `cited`: the share of proposals whose every cited id was in what the watch was shown.

They are kept in `ops_eval` as `shadow:watch` with the bundle, which the Detection page reads. With a scripted model the
numbers test the wiring, not the agent: a live score needs a provider key and is run by hand
(`fuzer eval --watch`). An exception counts as catching an upset only when its first sighting falls in the upset's
time, so one raised by a load stamped with the wall clock is missed, and the uncaught set is then too large.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..ops import sre, watch
from ..sources import process_model
from . import replay

KEY = "shadow:watch"
BAR = 0.5  # the epic's done-when: at least half the proposals match injected events no rule caught


def _t(v) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return watch._t(v)


def units_of(spec: dict) -> dict[str, str]:
    """Each name a proposal may use for something in the model, to its unit: a unit itself, or a point's tag."""
    out = {u: u for u in spec["units"]}
    out |= {p: u for p, (u, _) in spec["points"].items()}
    return out


def _near(a: datetime, b: datetime, e: dict) -> bool:
    return a <= e["end"] + replay.TAIL and b >= e["start"] - replay.LEAD


def caught(spec: dict, events: list[dict], found: list[dict], raised: list[dict]) -> list[bool]:
    """Whether each event was caught: a detection (`{point, start, end}`, point by name) on one of its own units within
    it, or an exception (`{names, at}`) on one of them within it."""
    unit = units_of(spec)
    out = []
    for e in events:
        hit = any(unit.get(d["point"]) in e["own"] and _near(d["start"], d["end"] or d["start"], e) for d in found)
        hit = hit or any({unit.get(n) for n in x["names"]} & e["own"] and _near(x["at"], x["at"], e) for x in raised)
        out.append(hit)
    return out


def matches(spec: dict, p: dict, e: dict) -> bool:
    """Whether a proposal (its names already resolved to tags: `names`, and its time, or its window's) is about the
    event."""
    unit = units_of(spec)
    a = p.get("start") or p["since"]
    b = p.get("end") or (p["until"] if not p.get("start") else a)
    return bool({unit.get(n) for n in p["names"]} & e["own"]) and _near(a, b, e)


def score(spec: dict, proposals: list[dict], found: list[dict], raised: list[dict], since: datetime,
          until: datetime) -> dict:
    """The proposals against the model's upsets in the period, split by whether a rule caught them."""
    events = [e for e in replay.truth(spec, since, until) if e["upset"]]
    got = caught(spec, events, found, raised)
    uncaught = [e for e, c in zip(events, got) if not c]
    on_caught = [e for e, c in zip(events, got) if c]
    m_un = [p for p in proposals if any(matches(spec, p, e) for e in uncaught)]
    m_c = [p for p in proposals if p not in m_un and any(matches(spec, p, e) for e in on_caught)]
    false = [p for p in proposals if p not in m_un and p not in m_c]
    n = len(proposals)
    found_un = [e for e in uncaught if any(matches(spec, p, e) for p in proposals)]

    def share(k, of):
        return round(k / of, 3) if of else None

    return {"upsets": len(events), "caught": len(on_caught), "uncaught": len(uncaught), "proposals": n,
            "matched_uncaught": len(m_un), "matched_caught": len(m_c), "false_proposals": len(false),
            "matched": share(len(m_un), n), "precision": share(len(m_un) + len(m_c), n),
            "recall": share(len(found_un), len(uncaught)), "cited": share(sum(bool(p.get("cited")) for p in proposals), n),
            "passes": n > 0 and len(m_un) / n >= BAR,
            "uncaught_examples": [f"{e['unit']} {e['kind']} {e['start']:%Y-%m-%d %H:%M}" for e in uncaught[:8]],
            "false_examples": [f"{p.get('subject')} {(p.get('start') or p['since']):%Y-%m-%d %H:%M}" for p in false[:8]]}


def _labels(conn, ids: set[str]) -> dict[str, str]:
    rows = conn.execute("select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),)).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["fuzer_id"], r["label"])
    return out


def _stored(conn, since: datetime, until: datetime) -> tuple[list[dict], list[dict]]:
    """The detections stored over the period (by point name) and the exceptions raised in it (by subject names)."""
    found = [{"point": r["point"], "start": r["start"], "end": r["end"]} for r in conn.execute(
        """
        select coalesce(l.label, d.point_id) as point, d.start, d."end" from detection d
        left join entity_label l on l.fuzer_id = d.point_id
        where d.start < %s and coalesce(d."end", d.start) >= %s
        """, (until + replay.TAIL, since - replay.LEAD))]
    rows = conn.execute("select subjects, first_seen from exception_record where first_seen < %s and first_seen >= %s",
                        (until + replay.TAIL, since - replay.LEAD)).fetchall()
    names = _labels(conn, {s for r in rows for s in r["subjects"]})
    raised = [{"names": {names.get(s, s) for s in r["subjects"]}, "at": _t(r["first_seen"])} for r in rows]
    return found, raised


def resolve(conn, proposals: list[dict]) -> list[dict]:
    """Each proposal with `names`: what it names and cites, ids turned into the names the model uses."""
    ids = {str(x) for p in proposals for x in [p.get("subject"), *(p.get("evidence") or [])] if x}
    lab = _labels(conn, ids)
    return [{**p, "names": {lab.get(x, x) for x in ids_ if x}}
            for p in proposals for ids_ in [[str(p.get("subject") or ""), *map(str, p.get("evidence") or [])]]]


def run(conn, pack, bundle: str, since: str, until: str, data_dir, ask=None, src=None,
        step: timedelta = timedelta(days=1), scan: bool = True, say=None) -> dict:
    """Scan the period with the rules, read it with the watch a `step` at a time, and score its proposals."""
    path = Path(data_dir or ".") / "process_model.json"
    if not path.is_file():
        raise ValueError("no process_model.json in the data folder: the watch is scored against a fake plant's events")
    spec = process_model.read(path)
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    if scan:
        sre.scan(conn, pack, since, until, src=src, max_days=max(1, (b - a).days + 1))
    proposals, usd, notes = [], 0.0, []
    t = a
    while t < b:
        w = min(t + step, b)
        out = watch.watch(conn, pack, t.isoformat(), w.isoformat(), ask=ask, src=src)
        if out["status"] != "shadow":
            return {"detector": KEY, "bundle": bundle, "since": since, "until": until, "status": out["status"],
                    "note": out["note"]}
        proposals += [{**p, "since": t, "until": w} for p in out["proposals"]]
        usd += out["usd"]
        notes += [out["note"]] if out["skipped"] else []
        if say:
            say(f"watch {bundle}: {w:%Y-%m-%d %H:%M}, {len(proposals)} proposals, ${usd:.2f}")
        t = w
    found, raised = _stored(conn, a, b)
    res = score(spec, resolve(conn, proposals), found, raised, a, b)
    return {"detector": KEY, "bundle": bundle, "since": since, "until": until, "status": "shadow",
            "model": getattr(ask, "model", None) or ("scripted" if ask else "provider"), "usd": round(usd, 4),
            "notes": notes, **res}


def store(conn, res: dict) -> None:
    """Keep a run's scores as `shadow:watch`, with its bundle, for the Detection page."""
    conn.execute("insert into ops_eval (detector, since, until, scores) values (%s, %s, %s, %s)",
                 (KEY, res["since"], res["until"], json.dumps(res, default=str)))
    conn.commit()


def judge(conn, data_dir, since: str, until: str, by: str = "replay") -> dict:
    """Confirm or reject the proposals kept over [since, until] that have no verdict, by the bundle's events (P3.4): one
    matching an upset is confirmed, with the event's kind; one matching none is rejected."""
    from ..ops import promote

    spec = process_model.read(Path(data_dir) / "process_model.json")
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    events = [e for e in replay.truth(spec, a - replay.TAIL, b + replay.TAIL) if e["upset"]]
    rows = [dict(r) for r in conn.execute(
        "select * from watch_proposal where verdict is null and since >= %s and until <= %s order by id", (a, b))]
    confirmed, rejected = [], []
    for p in resolve(conn, [{**r, "start": _t(r["start"]), "end": _t(r["end"]), "since": _t(r["since"]),
                             "until": _t(r["until"])} for r in rows]):
        hits = [e for e in events if matches(spec, p, e)]  # the most specific: on the fewest units (the machine, not its train)
        hit = min(hits, key=lambda e: (len(e["own"]), e["start"])) if hits else None
        promote.confirm(conn, p["id"], hit is not None, by, event_kind=hit["kind"] if hit else None)
        (confirmed if hit else rejected).append(p["id"])
    return {"confirmed": confirmed, "rejected": rejected}


def rule_score(conn, pack, spec: dict, window: tuple[str, str], data_dir, kinds: set[str], src=None) -> dict:
    """A promoted rule (P3.4) on the bundle's replay over `window`: recall over the event kinds its examples matched
    (all upsets when none is known), precision and false alarms a week."""
    from ..ops import promote

    def fn(conn, pack, a, b, src):
        return [d | {"detector": "promoted_rules"} for d in promote.apply(conn, spec, a, b, src)]

    fn.__name__ = "promoted_rule"
    res = replay.run(conn, pack, fn, window[0], window[1], data_dir=data_dir, src=src, learn_days=None)
    by = {k: v for k, v in res["by_kind"].items() if not kinds or k in kinds}
    events, found = sum(v["events"] for v in by.values()), sum(v["detected"] for v in by.values())
    return {"since": window[0], "until": window[1], "kinds": sorted(kinds), "events": events, "detected": found,
            "recall": round(found / events, 3) if events else None, "precision": res["precision"],
            "false_alarms_per_week": res["false_alarms_per_week"], "detections": res["detections"]}


def rule_scorer(conn, pack, data_dir, window: tuple[str, str], src=None):
    """What `promote.draft` takes to score a draft on the bundle's replay over `window`; None without a process model."""
    if not data_dir or not (Path(data_dir) / "process_model.json").is_file():
        return None
    return lambda spec, kinds: rule_score(conn, pack, spec, window, data_dir, kinds, src)


def score_drafts(conn, pack, data_dir, window: tuple[str, str], src=None) -> list[dict]:
    """Score each drafted rule that has no score yet on the bundle's replay over `window` (drafts made on the Detection
    page are unscored: the replay is an eval, run from the command line)."""
    from ..ops import promote

    scorer = rule_scorer(conn, pack, data_dir, window, src)
    if scorer is None:
        raise ValueError("no process_model.json in the data folder: a draft is scored against a fake plant's events")
    out = []
    for r in conn.execute("select * from promoted_rule where status = 'draft' and score is null order by id").fetchall():
        kinds = {e.get("event_kind") for e in r["examples"] or [] if e.get("event_kind")}
        out.append(promote.rescore(conn, r["id"], scorer(r["spec"], kinds)))
    return out

"""The agentic watch scored in shadow on replay (P3.3b, #931): does what it proposes match the bundle's injected events
that no rule caught?

- The SRE's scheduled scan runs over the period first, and the slow-drift detector (P3.11a), so the watch sees the
  detections the rules made (and leaves them alone, or reads a drift as evidence), as it would on the day.
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

Each bundle holds a fixed set of at least five windows in `evals/watch.yaml` (P3.11b), each holding an upset no rule
catches; `run_set` scores the watch on every one and over the whole set, where the pass bar is read (P3.11c): at least
half of all its proposals on what no rule caught, on each bundle.

They are kept in `ops_eval` as `shadow:watch` with the bundle, which the Detection page reads. With a scripted model the
numbers test the wiring, not the agent: a live score needs a provider key and is run by hand
(`fuzer eval --watch`). An exception counts as catching an upset only when its first sighting falls in the upset's
time, so one raised by a load stamped with the wall clock is missed, and the uncaught set is then too large.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..ops import drift, sre, watch
from ..sources import process_model
from . import replay

KEY = "shadow:watch"
BAR = 0.5  # the epic's done-when: at least half the proposals match injected events no rule caught
UPSETS = replay.UPSETS | replay.SLOW  # what the watch is for includes the slow kinds the SRE's replay leaves out
FILE = "watch.yaml"  # a bundle's fixed set of windows (P3.11b)
USD_PER_WINDOW = 0.35  # what a window has cost on a live model, before any run of the set is recorded


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
    events = [e for e in replay.truth(spec, since, until, UPSETS) if e["upset"]]
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
            "found_uncaught": len(found_un), "matched_uncaught": len(m_un), "matched_caught": len(m_c), "false_proposals": len(false),
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


SPLIT = re.compile(r"\s*(?:/|,|;|\(|\)|\band\b)\s*")  # a subject naming two things: "A1-FT-1010 / D1-FT-4002"


def _named(p: dict) -> list[str]:
    """What a proposal names and cites: each thing its subject names, and its evidence, a check by its equipment."""
    out = [x for x in SPLIT.split(str(p.get("subject") or "")) if x]
    return out + [x.rsplit(":", 1)[-1] if x.startswith("check:") else x for x in map(str, p.get("evidence") or [])]


def resolve(conn, proposals: list[dict]) -> list[dict]:
    """Each proposal with `names`: what it names and cites, ids turned into the names the model uses, and a tag named
    by its label carrying its historian points' names too (the model names a meter by its point)."""
    ids = {x for p in proposals for x in _named(p)}
    lab = _labels(conn, ids)
    shown = ids | set(lab.values())
    pts: dict[str, set[str]] = {}
    for r in conn.execute(
            """select l.label, pl.label as point from entity_label l
               join edge e on e.src = l.fuzer_id and e.rel = 'has_timeseries'
               join entity_label pl on pl.fuzer_id = e.dst where l.label = any(%s)""", (list(shown),)):
        pts.setdefault(r["label"], set()).add(r["point"])
    return [{**p, "names": {n for x in _named(p) for y in [lab.get(x, x)] for n in {y, *pts.get(y, ())}}}
            for p in proposals]


def run(conn, pack, bundle: str, since: str, until: str, data_dir, ask=None, src=None,
        step: timedelta = timedelta(days=1), scan: bool = True, say=None, drifts: bool = True) -> dict:
    """Scan the period with the rules (and with `drifts`, the slow-drift detector, P3.11a), read it with the watch a
    `step` at a time, and score its proposals."""
    path = Path(data_dir or ".") / "process_model.json"
    if not path.is_file():
        raise ValueError("no process_model.json in the data folder: the watch is scored against a fake plant's events")
    spec = process_model.read(path)
    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    if scan:
        sre.scan(conn, pack, since, until, src=src, max_days=max(1, (b - a).days + 1))
    if scan and drifts:  # the watch reads the drifts it raised as evidence, and a drift it raised is caught
        drift.peer_drifts(conn, pack, since, until, src)
    proposals, usd, notes = [], 0.0, []
    t = a
    while t < b:
        w = min(t + step, b)
        out = watch.watch(conn, pack, t.isoformat(), w.isoformat(), ask=ask, src=src, budget=False)
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


class Offline:
    """The watch's model, answered outside the run (a Claude session on a subscription, not the metered API): each
    question the watch asks is written to `<folder>/prompts/<hash>.txt`, and answered from `<folder>/answers/<hash>.md`
    once someone has written it. A question not yet answered reads as no proposals and is counted in `missing`, so a
    first run writes the questions and a second, with every answer in, scores them. The prompts are the ones the watch
    sends a provider, word for word; only who answers differs. A run with any question missing doesn't pass, and an
    offline run never earns live mode (`watch.bar_met` reads only runs on the provider)."""

    model = "offline"

    def __init__(self, folder):
        self.folder = Path(folder)
        self.missing: set[str] = set()
        self.asked: set[str] = set()

    # Entity ids are minted at random when a database is built (`graph.resolve`), and detections and exceptions are
    # numbered in the order they land, so the same window asks the same question with different ids on each build. The
    # key numbers ids by first appearance instead, and an answer is read back with the ids of the prompt it was written
    # for swapped, in the same order, for this run's.
    ID = re.compile(r"\bfz-[a-z_]+-[0-9a-f]{12}\b|\b(?:detection|exception):\d+\b")

    @classmethod
    def ids(cls, prompt: str) -> list[str]:
        return list(dict.fromkeys(cls.ID.findall(prompt)))

    @classmethod
    def key(cls, prompt: str) -> str:
        import hashlib

        n = {e: f"fz-id-{i}" for i, e in enumerate(cls.ids(prompt))}
        return hashlib.sha256(cls.ID.sub(lambda m: n[m.group()], prompt).encode()).hexdigest()[:16]

    def __call__(self, prompt: str) -> dict:
        k = self.key(prompt)
        self.asked.add(k)
        answer, asked = self.folder / "answers" / f"{k}.md", self.folder / "prompts" / f"{k}.txt"
        if answer.is_file():
            text = answer.read_text()
            if asked.is_file():
                swap = dict(zip(self.ids(asked.read_text()), self.ids(prompt), strict=True))
                text = self.ID.sub(lambda m: swap.get(m.group(), m.group()), text)
            return {"answer": text, "usage": {"cost_usd": 0.0}}
        asked.parent.mkdir(parents=True, exist_ok=True)
        asked.write_text(prompt)
        self.missing.add(k)
        return {"answer": '{"proposals": []}', "usage": {"cost_usd": 0.0}}


def windows(bundle) -> list[dict]:
    """A bundle's fixed set of windows (`evals/watch.yaml`, P3.11b), each {since, until, why}, its dates placed on the
    calendar (relative to today where the bundle's data is); none when it declares none."""
    import yaml

    from ..sources import relative_dates

    path = Path(bundle) / "evals" / FILE
    if not path.is_file():
        return []
    out = relative_dates.resolve_all(yaml.safe_load(path.read_text()) or {})
    return [{"since": str(w["since"])[:16].replace(" ", "T"), "until": str(w["until"])[:16].replace(" ", "T"),
             "why": w.get("why")} for w in out.get("windows") or []]


def estimate(wins: list[dict], recorded: list[dict]) -> float:
    """What scoring `wins` on a live model would cost: the windows the recorded runs scored, at what they cost each,
    else USD_PER_WINDOW a window."""
    per = [w["usd"] for r in recorded if r.get("kind") == "watch" for w in (r.get("windows") or [r])
           if w.get("usd")]
    return round(len(wins) * (sum(per) / len(per) if per else USD_PER_WINDOW), 2)


SUMMED = ("upsets", "caught", "uncaught", "proposals", "found_uncaught", "matched_uncaught", "matched_caught",
          "false_proposals")


def run_set(conn, pack, bundle: str, wins: list[dict], data_dir, ask=None, src=None, say=None) -> dict:
    """Score the watch on each of a bundle's windows and over the whole set (P3.11b, c): the pass bar is read from the
    set, at least half of all its proposals on what no rule caught."""
    each = []
    for w in wins:
        r = run(conn, pack, bundle, w["since"], w["until"], data_dir, ask=ask, src=src, say=say)
        if r["status"] != "shadow":
            return r
        each.append({**r, "why": w.get("why")})
        if say:
            say(f"watch {bundle} {w['since'][:10]}: {r['proposals']} proposals, {r['matched_uncaught']} on what no rule "
                f"caught, ${r['usd']:.2f}")
    tot = {k: sum(r[k] for r in each) for k in SUMMED}
    n = tot["proposals"]

    def share(k, of):
        return round(k / of, 3) if of else None

    keep = ("since", "until", "why", "usd", "passes", "matched", "precision", "recall", *SUMMED, "uncaught_examples",
            "false_examples")
    return {"detector": KEY, "bundle": bundle, "status": "shadow", "since": min(w["since"] for w in wins),
            "until": max(w["until"] for w in wins), "model": each[0]["model"] if each else None,
            "usd": round(sum(r["usd"] for r in each), 4), **tot,
            "matched": share(tot["matched_uncaught"], n), "precision": share(tot["matched_uncaught"] + tot["matched_caught"], n),
            "recall": share(tot["found_uncaught"], tot["uncaught"]),
            "cited": share(sum((r["cited"] or 0) * r["proposals"] for r in each), n),
            "passes": n > 0 and tot["matched_uncaught"] / n >= BAR and not getattr(ask, "missing", None),
            "uncaught_examples": [x for r in each for x in r["uncaught_examples"]][:8],
            "false_examples": [x for r in each for x in r["false_examples"]][:8],
            "windows": [{k: r.get(k) for k in keep} for r in each], **_offline(ask)}


def _offline(ask) -> dict:
    """An offline run says so, with the questions it asked and those still unanswered; one with any missing fails."""
    if not isinstance(ask, Offline):
        return {}
    return {"offline": True, "asked": len(ask.asked), "missing": len(ask.missing)}


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
    events = [e for e in replay.truth(spec, a - replay.TAIL, b + replay.TAIL, UPSETS) if e["upset"]]
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

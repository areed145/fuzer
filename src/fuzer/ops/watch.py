"""The agentic watch (P3.3a, #931): an agent that reads one process area at a time, as an engineer scanning the day
would, and proposes what looks unusual that no detector raised: with the evidence it read, why it is unusual and
what it ruled out.

- `evidence(conn, area, since, until)`: what the agent is shown for the area over the window:
  - the stretches its own PCA model of the week before can't explain (`clusters.spe`), with the points contributing
    most. These are read at a lower bar than the multivariate detector's, since the watch looks at what fell short
    of it.
  - the points that have drifted on their own over the months before (`clusters.drifts`, `DRIFT_DAYS`): against
    where they sat at the start and against their peers measuring the same thing, so a choke plugging or a pump losing
    head over weeks shows though the week's model has learned it as normal (P3.3c).
  - the checks against the equipment's own OEM curves that no rule raises, where one is flagged (`CHECKS`): a pump
    losing head, a turbine's heat rate rising, a choke eroding.
  - the detections already made on its points, and the open exceptions on its plant, so it can leave those alone. A
    slow drift the drift detector raised (`peer_drifts`, P3.11a) carries its baseline, its level now and how far its
    peers moved, so the watch explains the drift and what it reaches instead of having to find it.
  - the alarms that sounded and the operator log entries about it (`around`).
  - the maintenance notifications raised about it.
  Each item carries the id the agent must cite.
- `propose(...)`: one question to the model with that evidence. The answer is a JSON block of proposals. A proposal
  on something a detection already covers in its window is dropped, because the watch adds only what no detector
  raised. A proposal citing an id it wasn't shown is kept and marked uncited.
- `watch(...)`: the areas in turn (`watch.max_areas`), under a spend cap per run (`watch.max_usd`). In shadow
  (wave 4) each proposal is kept in `watch_proposal` and scored on replay (P3.3b). It is never routed: no exception,
  no case. With no model provider configured the watch is off and says so, and nothing else changes.

The model is a callable `ask(prompt) -> {"answer", "usage": {"cost_usd"}}`, as the SRE hand-off's is, so a scripted one
tests the wiring without a provider.
"""

import json
import re
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ..core import clock, detectors, llm, settings
from . import around

SPE_QUANTILE = 99.0  # the multivariate detector's bar is 99.9: the watch reads below it
SPE_MINUTES = 30
TRAIN_DAYS = 7
DRIFT_DAYS = 90  # how far back a drift is measured from
DRIFT_MIN_CHANGE = 0.03  # a drift shown: 3% of its level, below the detector's bar, as the watch reads below it
WELL_MIN_CHANGE = 0.1  # but a well's rate: each well wanders its own way by a few percent as the reservoir does
WELL_SET = {"gas_lift_rate"}  # a well's lift gas is set by hand
DRIFT_FIELDS = ("baseline", "now", "change", "peers_change", "peers", "role", "baseline_from")  # shown of a drift
# Calculations against the equipment's own OEM curves that no rule raises: a pump's head at its flow, a turbine's heat
# rate at its load and ambient, a choke's effective Cv, a stage's efficiency, a cooler's approach, a reciprocating
# compressor's valves, a glycol reboiler, two meters on one stream. A flag on one is a
# slow loss the points alone hide, so the watch is shown each one flagged.
CHECKS = ("pump_curve", "heat_rate", "choke", "compressor_performance", "cooler_fouling", "valve_health", "regenerator",
          "meter_agreement")
MAX_EACH = 15  # of each kind of evidence, so one noisy area doesn't fill the question
MAX_TOKENS = 2000
OFF = "No model provider is configured, so the agentic watch is off. Nothing else changes."

GUIDANCE = Path(__file__).with_name("watch_guidance")  # the watch's instructions, kept apart to lift and shift
PROMPT = (GUIDANCE / "prompt.md").read_text()  # a str.format template: {area}, {since}, {until}, {evidence}


def _iso(t) -> str | None:
    return t.isoformat(timespec="minutes") if isinstance(t, datetime) else (str(t)[:16] if t else None)


def areas(conn, limit: int | None = None) -> list[dict]:
    """The process areas with a model's worth of points (10 to 300), as the multivariate detector reads them."""
    rows = conn.execute(
        """
        select a.fuzer_id as id, coalesce(l.label, a.fuzer_id) as label, count(distinct t.dst) as points
        from entity a
        join edge p on p.dst = a.fuzer_id and p.rel = 'part_of'
        join edge t on t.src = p.src and t.rel = 'has_timeseries'
        left join lateral (select label from entity_label where fuzer_id = a.fuzer_id order by label limit 1) l on true
        where a.kind = 'area' and a.retired_at is null
        group by a.fuzer_id, l.label having count(distinct t.dst) between 10 and 300 order by 2
        """).fetchall()
    return [dict(r) for r in rows][:limit]


def _plant(conn, area: str) -> tuple[set[str], list[str]]:
    """The area's tags (what its alarms, log entries and notifications are about) and their historian points."""
    tags = {r["src"] for r in conn.execute("select src from edge where dst = %s and rel = 'part_of'", (area,))}
    points = [r["dst"] for r in conn.execute(
        "select distinct dst from edge where src = any(%s) and rel = 'has_timeseries' order by 1", (list(tags),))]
    return tags, points


def evidence(conn, area: dict, since: datetime, until: datetime, src=None) -> dict:
    """What the watch agent is shown for `area` ({id, label}) over [since, until], each item with the id to cite."""
    from ..analysis import calcs, clusters, records

    tags, points = _plant(conn, area["id"])
    spe = []
    try:
        r = clusters.spe(conn, area["id"], (_iso(since - timedelta(days=TRAIN_DAYS)), _iso(since)),
                         (_iso(since), _iso(until)), src=src, quantile=SPE_QUANTILE, min_minutes=SPE_MINUTES)
        spe = [{"start": x["start"], "end": x["end"], "peak_spe": round(x["peak_spe"], 1),
                "points": [{"id": p["point_id"], "name": p["point"]} for p in x["top_points"][:3]]}
               for x in r["anomalies"]][:MAX_EACH]
    except calcs.CalcError:
        pass
    dets = [{"id": f"detection:{d['id']}", "point_id": d["point_id"], "point": d["point"], "detector": d["detector"],
             "what": d["key"], "start": _iso(d["start"]), "end": _iso(d["end"]),
             **({"drift": {k: d["evidence"].get(k) for k in DRIFT_FIELDS}} if d["detector"] == "peer_drifts" else {})}
            for d in conn.execute(
        """
        select d.id, d.point_id, coalesce(l.label, d.point_id) as point, d.detector, d.key, d.start, d."end", d.evidence
        from detection d left join entity_label l on l.fuzer_id = d.point_id
        where d.point_id = any(%s) and d.start < %s and coalesce(d."end", d.start) >= %s order by d.start, point, d.detector, d.key, d.id limit %s
        """, (points, until, since, MAX_EACH))]
    drifts = [{"id": d["point_id"], "name": d["point"], "baseline_from": d["since"], "baseline": d["baseline"], "now": d["now"],
               "change": d["change"], "peers_change": d["peers_change"], "peers": d["peers"], "stepwise": d["stepwise"],
               **({"choke_moved": d["choke_moved"]} if "choke_moved" in d else {})}
              for d in sorted(_drifts(conn, area["id"], since, until, src),  # ties by name, so a prompt is the same
                              key=lambda d: (-round(abs(d["change"] - d["peers_change"]), 3), d["point"]))][:MAX_EACH]
    if drifts:  # what the operators wrote about each drifting point's equipment over the drift: a choke they moved
        owner = {r["dst"]: r["src"] for r in conn.execute(
            "select src, dst from edge where dst = any(%s) and rel = 'has_timeseries'", ([d["id"] for d in drifts],))}
        for d in drifts:
            d["logged"] = [{"id": e["id"], "at": e["at"], "text": e["text"]} for e in around.log(
                conn, {owner.get(d["id"], d["id"])}, since - timedelta(days=DRIFT_DAYS), until)][-3:]
    checks = _checks(conn, tags, until)
    open_x = [{"id": f"exception:{x['id']}", "type": x["type"], "title": x["title"]} for x in conn.execute(
        "select id, type, title from exception_record where state = 'open' and subjects && %s order by id limit %s",
        (list(tags | set(points)), MAX_EACH))]
    alarms = [{"id": a["point_id"], "point": a["point"], "alarm": a["type"], "limit": a["limit"], "peak": a["peak"],
               "start": a["start"], "end": a["end"]}
              for a in sorted(around.alarms(conn, tags, since, until, src),
                              key=lambda a: (str(a["start"]), a["point"], a["type"]))][:MAX_EACH]
    log = [{"id": e["id"], "at": e["at"], "text": e["text"]} for e in around.log(conn, tags, since, until)][:MAX_EACH]
    notes = [{"id": n["id"], "notification": n["label"], "created": n["f"].get("created"),
              "text": n["f"].get("description") or n["f"].get("short_text")}
             for n in records._rows(conn, "notification", "created", since.date().isoformat(), _iso(until), "concerns")
             if tags & set(n["subjects"])][:MAX_EACH]
    return {"area": area["label"], "since": _iso(since), "until": _iso(until), "unexplained_stretches": spe,
            "drifts": drifts, "checks": checks, "detections": dets, "open_exceptions": open_x, "alarms": alarms, "log": log, "notifications": notes}


def _checks(conn, tags: set[str], until: datetime) -> list[dict]:
    """Each of the area's equipment `CHECKS` flags as of `until`, worked out as the engineer's calc tool works it out;
    a check that doesn't apply to a tag, or can't answer from its data, is passed over."""
    from ..analysis import calcs

    out = []
    for check in CHECKS:
        for tag in sorted(tags):
            try:
                r = calcs.run(conn, check, tag, until=_iso(until))
            except calcs.CalcError:
                continue
            if r.get("flag"):
                out.append({"id": f"check:{check}:{tag}", "equipment": r["of"]["label"], "check": check,
                            "flag": r["flag"], "value": r.get("value"), "expected": r.get("expected"),
                            "deviation_pct": r.get("deviation_pct"), "basis": r.get("basis")})
    return sorted(out, key=lambda c: (CHECKS.index(c["check"]), c["equipment"]))[:MAX_EACH]


def _shown(d: dict, role: str, cls: str | None) -> bool:
    """Whether a drift is worth the watch's question: 3% beyond its peers' move, or on a well only its rate, by 10%. A
    well's pressures wander with the reservoir, each its own way, and its rate at the same choke says first what a
    plugging choke or a loading well does."""
    from ..core import roles

    own = abs(d["change"] - d["peers_change"])
    if cls != "well":
        return own >= DRIFT_MIN_CHANGE
    r = roles.ROLES.get(role)
    return r is not None and r.quantity == "flow" and role not in WELL_SET and own >= WELL_MIN_CHANGE


def _drifts(conn, area: str, since: datetime, until: datetime, src) -> list[dict]:
    """The area's points drifting on their own, judged as the drift detector judges them where measurement roles are
    known (`drift.watched`): only the points it reads (not a position, a speed, the weather or a wellhead's
    temperature), each set against its role on its class of equipment rather than any point with the same suffix, a
    stopped or shut-in stretch left out, and one coming back to normal not taken for a drift. One on equipment whose
    choke or valve the operators moved over the months is marked `choke_moved` (`drift.moved_by_hand`): the detector
    leaves it alone, but a choke opened to hold a rate that still falls is the plugging it hides. A whole field declining
    or a season warming moves the peers too, so it isn't shown."""
    from ..analysis import clusters
    from . import drift

    judge: dict = {}
    if conn.execute(drift.ROLES).fetchone():
        _, peers, asset, hand = drift.watched(conn)
        judge = {"peers_by": peers, "idle": drift.IDLE, "min_peers": drift.MIN_PEERS, "progressive": True}
    found = clusters.drifts(conn, area, _iso(since), _iso(until), days=DRIFT_DAYS, src=src, limit=None,
                           min_change=DRIFT_MIN_CHANGE if judge else 0.1, **judge)
    if not judge:
        return found
    found = [d for d in found if d["point_id"] in peers and _shown(d, *peers[d["point_id"]])]
    pos = sorted({i for d in found for i in hand.get(asset.get(d["point_id"]), [])})
    if pos:  # on equipment whose choke or valve the operators moved over the months: perhaps set by hand
        from ..analysis import studies
        from ..sources import timeseries

        pts = list(timeseries._points(conn, pos).values())
        times, X = studies.matrix(pts, since - timedelta(days=DRIFT_DAYS), until, 360, src)
        days = times.astype("datetime64[D]")  # each day's level, as the drift detector keeps it
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            X = np.array([np.nanmedian(X[days == d], axis=0) for d in np.unique(days)]).reshape(-1, len(pts))
        moved = {asset[i] for i in drift.moved_by_hand(X, [p["id"] for p in pts])}
        for d in found:
            d["choke_moved"] = asset.get(d["point_id"]) in moved
    return found


def _ids(ev: dict) -> set[str]:
    out = set()
    for k in ("detections", "open_exceptions", "alarms", "log", "notifications"):
        out |= {str(x["id"]) for x in ev[k]}
    for s in ev["unexplained_stretches"]:
        out |= {p["id"] for p in s["points"]} | {p["name"] for p in s["points"]}
    out |= {d["id"] for d in ev.get("drifts", [])} | {d["name"] for d in ev.get("drifts", [])}
    out |= {c["id"] for c in ev.get("checks", [])} | {c["equipment"] for c in ev.get("checks", [])}
    out |= {str(e["id"]) for d in ev.get("drifts", []) for e in d.get("logged", [])}
    out |= {d["point"] for d in ev["detections"]} | {a["point"] for a in ev["alarms"]}
    return out


def _parse(answer: str) -> list[dict]:
    m = re.search(r"```json\s*(\{.*?\})\s*```", answer, re.DOTALL) or re.search(r"(\{\s*\"proposals\".*\})", answer,
                                                                                 re.DOTALL)
    if not m:
        return []
    try:
        got = json.loads(m.group(1)).get("proposals") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    return [p for p in got if isinstance(p, dict) and p.get("why")]


def _t(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except ValueError:
        return None


def _covered(p: dict, ev: dict) -> bool:
    """Whether a detection already made covers the proposal: on what it names or cites, overlapping its window."""
    names = {str(p.get("subject") or "")} | {str(x) for x in p.get("evidence") or []}
    a, b = _t(p.get("start")), _t(p.get("end")) or _t(p.get("start"))
    for d in ev["detections"]:
        if not ({d["point"], d["point_id"], d["id"]} & names):
            continue
        da, db = _t(d["start"]), _t(d["end"]) or _t(d["start"])
        if a is None or da is None or (da <= (b or a) and a <= (db or da)):
            return True
    return False


def _explained(conn, ev: dict) -> list[dict]:
    """The unexplained stretches on equipment a detection already covers in their time, or next to it in the plant
    model (one of its train, what it is part of) (P3.4): a rule caught it, so there is nothing for the watch to add."""
    from ..analysis import limits
    from ..graph.topology import topology

    if not ev["detections"] or not ev["unexplained_stretches"]:
        return []
    pts = {p["id"] for s in ev["unexplained_stretches"] for p in s["points"]} | {d["point_id"] for d in ev["detections"]}
    unit, adj = limits.units(conn, list(pts)), topology(conn)
    out = []
    for s in ev["unexplained_stretches"]:
        on = {unit.get(p["id"]) for p in s["points"]} - {None}
        on |= {n for u in on for n in adj.get(u, ())}
        a, b = _t(s["start"]), _t(s["end"]) or _t(s["start"])
        for d in ev["detections"]:
            da, db = _t(d["start"]), _t(d["end"]) or _t(d["start"])
            if unit.get(d["point_id"]) in on and a and da and da <= b and a <= db:
                out.append(s)
                break
    return out


def _undetected(conn, ev: dict) -> list[dict]:
    """The drifts on equipment no detection in the window is on: one a rule already raised is not the watch's."""
    from ..analysis import limits

    if not ev["drifts"] or not ev["detections"]:
        return ev["drifts"]
    unit = limits.units(conn, [d["id"] for d in ev["drifts"]] + [d["point_id"] for d in ev["detections"]])
    caught = {unit.get(d["point_id"]) for d in ev["detections"]} - {None}
    return [d for d in ev["drifts"] if unit.get(d["id"]) not in caught]


def propose(conn, area: dict, since: datetime, until: datetime, ask, src=None) -> tuple[list[dict], float]:
    """The model's proposals for one area and window that no detection covers, and what asking cost. The model is
    asked only when a stretch the area's model can't explain, a drift not set by hand (`stepwise`) or a flagged check
    is left once those a detection covers are
    set aside: the rest of the evidence is read beside one, never alone."""
    ev = evidence(conn, area, since, until, src)
    covered = _explained(conn, ev)
    ev["unexplained_stretches"] = [s for s in ev["unexplained_stretches"] if s not in covered]
    ev["drifts"] = _undetected(conn, ev)
    if not ev["unexplained_stretches"] and not ev["checks"] and all(d["stepwise"] for d in ev["drifts"]):
        return [], 0.0  # nothing unexplained, drifting or flagged, or a detection covers it: no question asked
    r = ask(PROMPT.format(area=ev["area"], since=ev["since"], until=ev["until"], evidence=json.dumps(ev, indent=1)))
    usd = float((r.get("usage") or {}).get("cost_usd") or 0.0)
    shown, out = _ids(ev), []
    for p in _parse(r.get("answer") or ""):
        if _covered(p, ev):
            continue
        cites = [str(x) for x in p.get("evidence") or []]
        out.append({"area": area["id"], "area_label": area["label"], "subject": p.get("subject"),
                    "start": _t(p.get("start")), "end": _t(p.get("end")), "why": str(p["why"]),
                    "ruled_out": p.get("ruled_out"), "evidence": cites,
                    "cited": bool(cites) and set(cites) <= shown})
    return out, usd


def keep(conn, p: dict, since: datetime, until: datetime, model: str | None = None) -> dict:
    """Keep one proposal in shadow."""
    row = conn.execute(
        """
        insert into watch_proposal (area, since, until, subject, start, "end", why, ruled_out, evidence, cited, mode, model)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'shadow', %s) returning id
        """, (p["area"], since, until, p.get("subject"), p.get("start"), p.get("end"), p["why"], p.get("ruled_out"),
              json.dumps(p.get("evidence") or []), bool(p.get("cited")), model)).fetchone()
    return {**p, "id": row["id"]}


def model_ask(prompt: str) -> dict:
    """The configured agents provider, asked once."""
    client = llm.client("agents")
    name = settings.get(None, "agents.model")
    msg = llm.call(client, {"model": name, "max_tokens": MAX_TOKENS,
                            "messages": [{"role": "user", "content": prompt}]}, scope="agents")
    text = "\n".join(b.text for b in msg.content if getattr(b, "type", None) == "text")  # thinking blocks carry none
    return {"answer": text, "model": name,
            "usage": llm.cost_report(name, llm.usage(msg), scope="agents")}


def _provider(conn, pack) -> bool:
    return llm.configured("agents")


def bar_met(recorded: list[dict] | None = None) -> dict:
    """Whether the watch has earned live mode (P3.11): its latest recorded run over a bundle's fixed set of windows
    (`fuzer eval --watch --record`) passes the bar on every bundle that has one, at least half its proposals on what no
    rule caught. Read from the runs kept with the code, so every deployment sees the same evidence."""
    if recorded is None:
        from ..resources import ROOT

        recorded = [json.loads(f.read_text()) for f in sorted((ROOT / "evals" / "runs").glob("*-watch-*.json"))]
    latest = {r["bundle"]: r for r in recorded  # a run answered offline (watch_eval.Offline) never earns it
              if r.get("kind") == "watch" and r.get("windows") and not r.get("offline")}
    short = sorted(b for b, r in latest.items() if not r.get("passes"))
    return {"met": bool(latest) and not short, "bundles": sorted(latest), "short": short}


def spent_today(conn) -> float:
    """What the watch has spent on the model today on this asset."""
    r = conn.execute("select coalesce(sum(usd), 0) as usd from watch_spend where day = %s", (clock.now().date(),))
    return float(r.fetchone()["usd"])


WATCH_TYPE = "watch_finding"


def _watch_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("watch_finding") or exception_type(
        "watch_finding", "Proposed by the watch", role="role:engineer", audience=("ioc",), joins=False,
        description="The agentic watch, reading a process area as an engineer scanning the day would, proposed "
                    "something that looks unusual that no rule raised. It is a proposal, not a rule's finding: triage "
                    "it before anything is dispatched. Close it out as a real problem to confirm it (three confirmed "
                    "of one pattern draft a rule), or as a false alarm, which counts against the watch's score.")


_watch_type()


def route(conn, p: dict, by: str = "agent:watch") -> dict:
    """Raise a kept proposal as work for a person to triage (live mode): through the finding contract, marked as the
    watch's, to the engineer's queue with no process, so nothing is dispatched until a person decides."""
    from ..core.exceptions import raise_exception
    from ..core.findings import AGENTIC, Finding, asset, at, fact

    ids = [str(x) for x in [p.get("subject"), *(p.get("evidence") or [])] if x]
    known = [r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where fuzer_id = any(%s) union select fuzer_id from entity_label where label = any(%s)",
        (ids, ids))]
    found = Finding(
        "watch", AGENTIC, method="The agentic watch read the process area and proposed this; no rule raised it",
        start=p.get("start"), end=p.get("end"), confidence=None,
        items=[fact("Proposed by", "the agentic watch, not a rule: triage before dispatch"), fact("About", p.get("subject")),
               fact("Why it looks unusual", p.get("why")), fact("Ruled out", p.get("ruled_out")),
               at("From", p.get("start")), at("To", p.get("end")), asset("Area", p.get("area")),
               fact("Every cited record was shown to it", bool(p.get("cited")))],
        data={"proposal": p["id"], "evidence": p.get("evidence") or []})
    x = raise_exception(conn, WATCH_TYPE, f"watch:{p['id']}", f"Watch proposal: {p.get('subject') or p['area_label']}",
                        by, source="watch", refs=[p["area"], *known], priority=0.3, finding=found)
    conn.execute("update watch_proposal set mode = 'live' where id = %s", (p["id"],))
    return {**p, "exception_id": x["id"], "item_id": x.get("item_id")}


def on_closeout(conn, item_id: int, disposition: str, by: str) -> list[int]:
    """A case raised from the watch's proposals closed out: a real problem confirms each proposal (read into its
    pattern, P3.4), a false alarm rejects it and counts against the watch. Other close-outs say nothing of it."""
    from . import promote

    if disposition not in ("real", "false_alarm"):
        return []
    out = []
    for r in conn.execute("select key from exception_record where item_id = %s and type = %s", (item_id, WATCH_TYPE)):
        pid = int(r["key"].split(":", 1)[1])
        promote.confirm(conn, pid, disposition == "real", by)
        out.append(pid)
    return out


@detectors.register("watch", title="What looks unusual in a process area that no detector raised (agentic)",
                    kind=detectors.AGENTIC, cadence="daily", raises=(WATCH_TYPE,),
                    needs={"a model provider": _provider},
                    params=("watch.live", "watch.daily_usd", "watch.max_usd", "watch.max_areas"),
                    eval="test:tests/ops/test_watch.py::test_the_watch_in_shadow_keeps_proposals_and_routes_nothing",
                    found=lambda out: len(out.get("proposals", [])))
def watch(conn, pack=None, since: str | None = None, until: str | None = None, ask=None, src=None,
          budget: bool = True, recorded: list[dict] | None = None) -> dict:
    """Each process area over the window (the day to `until` by default) read by the watch agent under the run's spend
    cap and the asset's daily budget (`budget`; an eval replaying history has its own). In shadow its proposals are
    kept and scored, never routed. In live mode (`watch.live` on this asset, and only once the watch has passed its bar
    on every bundle, `bar_met`) each is also raised as work for a person to triage (`route`); with the bar not met it
    stays in shadow and says so."""
    if ask is None:
        if not llm.configured("agents"):
            return {"status": "off", "note": OFF, "proposals": []}
        ask = model_ask
    b = datetime.fromisoformat(until) if until else datetime.now().replace(minute=0, second=0, microsecond=0)
    a = datetime.fromisoformat(since) if since else b - timedelta(days=1)
    cap, spent, kept, skipped, calls = float(settings.get(conn, "watch.max_usd")), 0.0, [], [], []
    held = ""
    live = bool(settings.get(conn, "watch.live"))
    if live and not (bar := bar_met(recorded))["met"]:
        live, held = False, (" Live mode is on, but the watch has not passed its bar on every bundle yet"
                             + (f" (short on {', '.join(bar['short'])})" if bar["short"] else "") + ", so it stays in shadow.")
    left = float(settings.get(conn, "watch.daily_usd")) - spent_today(conn) if budget else float("inf")
    if left <= 0:
        return {"status": "budget", "since": _iso(a), "until": _iso(b), "proposals": [], "usd": 0.0, "skipped": [],
                "asked": 0, "note": "Today's budget for the watch is spent; it reads again tomorrow."}
    cap = min(cap, left)

    def counted(prompt: str) -> dict:  # each question asked, so a run says how many it spent on
        calls.append(prompt)
        return ask(prompt)

    for area in areas(conn, int(settings.get(conn, "watch.max_areas"))):
        if spent >= cap:
            skipped.append(area["label"])
            continue
        props, usd = propose(conn, area, a, b, counted, src)
        spent += usd
        kept += [keep(conn, p, a, b, getattr(ask, "model", None)) for p in props]
    if budget and spent:
        conn.execute("insert into watch_spend (day, usd, mode) values (%s, %s, %s)",
                     (clock.now().date(), spent, "live" if live else "shadow"))
    if live:
        kept = [route(conn, p) for p in kept]
    conn.commit()
    capped = (" today's budget" if cap < float(settings.get(conn, "watch.max_usd")) else " the run's spend cap")
    return {"status": "live" if live else "shadow", "since": _iso(a), "until": _iso(b), "proposals": kept,
            "usd": round(spent, 4), "skipped": skipped, "asked": len(calls),
            "note": ("Live: each proposal is raised for a person to triage, never dispatched on its own." if live else
                     "Shadow: proposals are kept and scored, not routed." + held) + (
                f" {len(skipped)} areas not read:{capped} was reached." if skipped else "")}

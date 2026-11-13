"""The agentic watch (P3.3a, #931): an agent that reads one process area at a time, as an engineer scanning the day
would, and proposes what looks unusual that no detector raised: with the evidence it read, why it is unusual and
what it ruled out.

- `evidence(conn, area, since, until)`: what the agent is shown for the area over the window:
  - the stretches its own PCA model of the week before can't explain (`clusters.spe`), with the points contributing
    most. These are read at a lower bar than the multivariate detector's, since the watch looks at what fell short
    of it.
  - the detections already made on its points, and the open exceptions on its plant, so it can leave those alone.
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
from datetime import datetime, timedelta

from ..core import detectors, llm, settings
from . import around

SPE_QUANTILE = 99.0  # the multivariate detector's bar is 99.9: the watch reads below it
SPE_MINUTES = 30
TRAIN_DAYS = 7
MAX_EACH = 15  # of each kind of evidence, so one noisy area doesn't fill the question
MAX_TOKENS = 2000
OFF = "No model provider is configured, so the agentic watch is off. Nothing else changes."

PROMPT = """You are watching one process area of an oil and gas facility, as an experienced process engineer scanning
the day. Below is what happened in it over the window: stretches its own multivariate model could not explain (with the
points contributing most), detections already made, open exceptions, alarms, operator log entries and maintenance
notifications. Each item has an id.

Propose what looks unusual that no detection or open exception already covers: an equipment problem developing, an
instrument going bad, a process change nobody recorded. For each, say why it is unusual, cite the ids of the evidence
it rests on (only ids given below), and say what you ruled out (normal operation, a planned change, a known cause).
Propose nothing when nothing is unusual: a quiet day is the common case, and every proposal costs a person's time.
Never give an operating instruction.

End with a JSON block:
```json
{{"proposals": [{{"subject": "<the point or equipment, as named below>", "start": "<ISO time>", "end": "<ISO time>",
  "why": "<one or two sentences>", "ruled_out": "<what you ruled out>", "evidence": ["<id>", "..."]}}]}}
```

Area: {area}
Window: {since} to {until}

Evidence:
{evidence}
"""


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
             "what": d["key"], "start": _iso(d["start"]), "end": _iso(d["end"])} for d in conn.execute(
        """
        select d.id, d.point_id, coalesce(l.label, d.point_id) as point, d.detector, d.key, d.start, d."end"
        from detection d left join entity_label l on l.fuzer_id = d.point_id
        where d.point_id = any(%s) and d.start < %s and coalesce(d."end", d.start) >= %s order by d.start limit %s
        """, (points, until, since, MAX_EACH))]
    open_x = [{"id": f"exception:{x['id']}", "type": x["type"], "title": x["title"]} for x in conn.execute(
        "select id, type, title from exception_record where state = 'open' and subjects && %s order by id limit %s",
        (list(tags | set(points)), MAX_EACH))]
    alarms = [{"id": a["point_id"], "point": a["point"], "alarm": a["type"], "limit": a["limit"], "peak": a["peak"],
               "start": a["start"], "end": a["end"]} for a in around.alarms(conn, tags, since, until, src)][:MAX_EACH]
    log = [{"id": e["id"], "at": e["at"], "text": e["text"]} for e in around.log(conn, tags, since, until)][:MAX_EACH]
    notes = [{"id": n["id"], "notification": n["label"], "created": n["f"].get("created"),
              "text": n["f"].get("description") or n["f"].get("short_text")}
             for n in records._rows(conn, "notification", "created", since.date().isoformat(), _iso(until), "concerns")
             if tags & set(n["subjects"])][:MAX_EACH]
    return {"area": area["label"], "since": _iso(since), "until": _iso(until), "unexplained_stretches": spe,
            "detections": dets, "open_exceptions": open_x, "alarms": alarms, "log": log, "notifications": notes}


def _ids(ev: dict) -> set[str]:
    out = set()
    for k in ("detections", "open_exceptions", "alarms", "log", "notifications"):
        out |= {str(x["id"]) for x in ev[k]}
    for s in ev["unexplained_stretches"]:
        out |= {p["id"] for p in s["points"]} | {p["name"] for p in s["points"]}
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


def propose(conn, area: dict, since: datetime, until: datetime, ask, src=None) -> tuple[list[dict], float]:
    """The model's proposals for one area and window that no detection covers, and what asking cost."""
    ev = evidence(conn, area, since, until, src)
    if not any(ev[k] for k in ("unexplained_stretches", "detections", "alarms", "log", "notifications")):
        return [], 0.0  # nothing happened to read: no question asked
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


def model_ask(prompt: str) -> dict:
    """The configured agents provider, asked once."""
    client = llm.client("agents")
    name = settings.get(None, "agents.model")
    msg = llm.call(client, {"model": name, "max_tokens": MAX_TOKENS,
                            "messages": [{"role": "user", "content": prompt}]}, scope="agents")
    return {"answer": llm._text(msg.content), "model": name,
            "usage": llm.cost_report(name, llm.usage(msg), scope="agents")}


def _provider(conn, pack) -> bool:
    return llm.configured("agents")


@detectors.register("watch", title="What looks unusual in a process area that no detector raised (agentic, in shadow)",
                    kind=detectors.AGENTIC, cadence="daily", raises=(),
                    needs={"a model provider": _provider},
                    params=("watch.max_usd", "watch.max_areas"),
                    eval="test:tests/ops/test_watch.py::test_the_watch_in_shadow_keeps_proposals_and_routes_nothing",
                    found=lambda out: len(out.get("proposals", [])))
def watch(conn, pack=None, since: str | None = None, until: str | None = None, ask=None, src=None) -> dict:
    """Each process area over the window (the day to `until` by default) read by the watch agent under the run's spend
    cap; its proposals kept in shadow, never routed."""
    if ask is None:
        if not llm.configured("agents"):
            return {"status": "off", "note": OFF, "proposals": []}
        ask = model_ask
    b = datetime.fromisoformat(until) if until else datetime.now().replace(minute=0, second=0, microsecond=0)
    a = datetime.fromisoformat(since) if since else b - timedelta(days=1)
    cap, spent, kept, skipped = float(settings.get(conn, "watch.max_usd")), 0.0, [], []
    for area in areas(conn, int(settings.get(conn, "watch.max_areas"))):
        if spent >= cap:
            skipped.append(area["label"])
            continue
        props, usd = propose(conn, area, a, b, ask, src)
        spent += usd
        for p in props:
            row = conn.execute(
                """
                insert into watch_proposal (area, since, until, subject, start, "end", why, ruled_out, evidence, cited,
                                            mode, model)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'shadow', %s) returning id
                """, (p["area"], a, b, p["subject"], p["start"], p["end"], p["why"], p["ruled_out"],
                      json.dumps(p["evidence"]), p["cited"], getattr(ask, "model", None))).fetchone()
            kept.append({**p, "id": row["id"]})
    conn.commit()
    return {"status": "shadow", "since": _iso(a), "until": _iso(b), "proposals": kept, "usd": round(spent, 4),
            "skipped": skipped,
            "note": "Shadow: proposals are kept and scored, not routed." + (
                f" {len(skipped)} areas not read: the run's spend cap was reached." if skipped else "")}

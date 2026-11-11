"""SRE hand-off (#96c): each upset of note gets a priority (#100), a diagnosis from Field Operations & Facilities
(Claude, within a per-run cap) and a work item in the `upset` process (#97), where an engineer triages it.

- priority: the #100 score of what the upset puts at stake. Its first-cause unit's oil lost per day to
  downtime, its criticality and failure rate count. An upset still going is due now; a recovered one is
  scored as the cost of a repeat.
- diagnose: one question to the operations assistant team, which routes it to Field Operations & Facilities. The
  answer gives likely causes with confidence, the records that support each, what would confirm or rule
  each out, and proposed actions for people (never plant operating instructions). A JSON block at the end
  makes these fields. A cause with no cited record is marked uncited.
- handoff: upsets not handed off yet (process upsets of `MIN_DETECTIONS` or more, not suppressed),
  highest priority first. Each is diagnosed while the run's cap allows, then starts or adds evidence to
  the `upset` process (correlation key: the upset). An upset over the cap still gets its work item; the
  field-operations specialist diagnoses it in the workflow after triage.
- feedback: an engineer's triage of "false alarm" marks the upset and suppresses its first detection's
  pattern (point, detector, finding), so the same pattern isn't handed off again.
"""

import json
import re
from datetime import datetime

from psycopg.types.json import Jsonb

from ..core import settings
from ..core.exceptions import ExceptionError, exception_type, raise_exception
from ..core.findings import DETERMINISTIC, Finding, asset, at, fact
from ..core.jsonsafe import plain as _plain

# Defaults; the values in effect are settings (#131): sre.handoff.min_detections, .max_diagnoses, .max_usd
UPSET = exception_type("upset", "Process upset", process="upset", audience=("ioc", "field"),
                       description="Detections grouped into one upset by time and plant topology.").name
MIN_DETECTIONS = 3  # a lone detection is evidence, not an upset worth a person's time
MAX_DIAGNOSES = 10  # per run
MAX_USD = 5.0  # per run
CITE = re.compile(r"\b(fz-[a-z_]+-[0-9a-f]{6,}|[a-z_]+:[a-z_]+:[A-Za-z0-9._#/-]+)")


# ---- priority -------------------------------------------------------------------------------------

def priority(conn, pack, upset: dict, today: str | None = None) -> dict:
    """The #100 score of one upset, with its breakdown."""
    from ..analysis import value
    from ..graph import operations

    cfg = value.config(pack)
    today = today or operations.as_of(conn)
    ctx = value._context(conn, today)
    src: dict = {}
    t = value._tag_inputs(ctx, upset["first_unit"], src)
    ongoing = upset["end"] is None
    fpy = t.get("failures_per_year")
    horizon = cfg["horizon_days"]
    if ongoing:
        chance, ttc = 1.0, 0.0
        src["time_to_consequence_days"] = "upset still going at the last scan"
    else:
        chance = min(1.0, (fpy or 0) * horizon / 365.25) if fpy is not None else None
        ttc = 365.25 / fpy if fpy else None
        src["chance"] = f"failures per year of the unit over {horizon} d"
    duration, notes = None, [f"reached {len(upset['units'])} units"] + ([] if ongoing else ["recovered: scored as a repeat"])
    # On producing wells, what's at stake is the production it defers (#455): while it lasts, or as often as it
    # has come back
    rate = rate_at_risk(conn, upset.get("units") or [upset["first_unit"]])
    times = 1 if ongoing else repeats(conn, upset)
    if rate and (ongoing or times >= 2):
        share = cfg["upsets"]["rate_at_risk_share"]
        oil, gas = rate["oil_sm3d"] * share, rate["gas_ksm3d"] * share
        if ongoing:
            days, how = cfg["upsets"]["days"], f"for {cfg['upsets']['days']:g} d while it lasts"
        else:
            lasted = max((upset["end"] - upset["start"]).total_seconds() / 86400, 1 / 24)
            days = lasted * times / REPEAT_DAYS * horizon
            how = (f"for {lasted * 24:,.1f} h each time, {times} times in the last {REPEAT_DAYS} d, "
                   f"so about {times / REPEAT_DAYS * horizon:,.0f} times in {horizon} d")
        if oil * days > (t.get("oil_sm3d") or 0) * horizon * (chance or 0):  # more than its unit's downtime history
            t["oil_sm3d"], t["gas_ksm3d"], duration, chance = oil, gas, days, 1.0
            src["oil_sm3d"] = (f"{share:.0%} of the latest tested rate of the {rate['wells']} producing well(s) it "
                               f"reaches ({rate['oil_sm3d']:,.1f} Sm3/d oil, {rate['gas_ksm3d']:,.1f} kSm3/d gas), {how}")
            src["chance"] = "still going" if ongoing else f"came back {times - 1} time(s) in {REPEAT_DAYS} d"
            notes = notes[:1] + ([] if ongoing else [f"recovered: {times} times in {REPEAT_DAYS} d"])
            if not ongoing:
                ttc = REPEAT_DAYS / times
                src["time_to_consequence_days"] = f"the next repeat, at the rate it came back in {REPEAT_DAYS} d"
    item = value.Item("upset", f"upset:{upset['id']}", upset.get("label") or f"upset {upset['id']}",
                      upset.get("text"), upset.get("unit_label"), upset["first_unit"], oil_sm3d=t.get("oil_sm3d"),
                      gas_ksm3d=t.get("gas_ksm3d"), duration_days=duration,
                      chance=chance, criticality=t.get("criticality"), failures_per_year=fpy,
                      time_to_consequence_days=ttc, sources=src, notes=notes)
    return value.score(item, cfg)


REPEAT_DAYS = 30  # a recovered upset is valued by how often the same finding came back over this many days


def repeats(conn, upset: dict) -> int:
    """How many upsets (this one too) started from the same point and finding in the REPEAT_DAYS up to its start."""
    if not upset.get("point_id"):
        return 1
    r = conn.execute(
        """
        select count(*) as n from upset u join detection d on d.id = u.first_detection
        where u.kind = 'process' and d.point_id = %s and d.key = %s
          and u.start between %s::timestamp - make_interval(days => %s) and %s
        """, (upset["point_id"], upset["key"], upset["start"], REPEAT_DAYS, upset["start"])).fetchone()
    return max(1, r["n"])


def rate_at_risk(conn, unit_ids: list[str]) -> dict | None:
    """The producing wells an upset reaches (its units that are wells, or equipment part of a well) and their
    latest EC well test: oil (Sm3/d) and gas (kSm3/d, the test's gas rate or oil x GOR). None when it reaches no
    tested producing well."""
    rows = conn.execute(
        """
        with wells as (
          select e.fuzer_id as well from entity e where e.kind = 'well' and e.fuzer_id = any(%(u)s)
          union select p.dst from edge p join entity w on w.fuzer_id = p.dst and w.kind = 'well'
          where p.rel = 'part_of' and p.src = any(%(u)s)),
        tests as (
          select t.dst as well, jsonb_object_agg(g.field, g.value) as f
          from edge t join golden g on g.fuzer_id = t.src
          where t.rel = 'tests' and t.dst in (select well from wells) group by t.dst, t.src)
        select distinct on (well) well, f from tests order by well, f->>'test_date' desc nulls last
        """, {"u": list(unit_ids)}).fetchall()
    from ..analysis.value import _num

    oil = gas = 0.0
    wells = 0
    for r in rows:
        q = _num(r["f"].get("oil_sm3d"))
        g = _num(r["f"].get("gas_ksm3d")) or q * _num(r["f"].get("gor_sm3_sm3")) / 1000.0
        if q > 0 or g > 0:
            oil, gas, wells = oil + q, gas + g, wells + 1
    return {"oil_sm3d": oil, "gas_ksm3d": gas, "wells": wells} if wells else None


# ---- diagnosis ------------------------------------------------------------------------------------

def prompt(conn, upset: dict) -> str:
    from . import sre

    d = sre.upset_detail(conn, upset["id"], limit=25)
    lines = [f"- {x['start']} {x['unit'] or '?'} {x['point']}: {x['detector']} {x['key']}"
             + (f" ({x['magnitude']})" if x["magnitude"] is not None else "") for x in d["timeline"]]
    units = ", ".join(f"{u} ({n})" for u, n in sorted(d["by_unit"].items(), key=lambda kv: -kv[1])[:15])
    return (
        f"The SRE detected an upset in the process data (upset {upset['id']}), not yet reported by anyone.\n"
        f"Likely first cause: {d['first_key']} on {d['first_point']} ({d['first_unit']}) at {d['start']}"
        f"{', still going' if d['end'] is None else ', ended ' + str(d['end'])}.\n"
        f"Detections per unit: {units}.\nFirst detections in time order:\n" + "\n".join(lines) + "\n\n"
        "Use Field Operations & Facilities to assess what most likely happened and why, with a confidence for each "
        "cause, the records that support it (cite Fuzer ids or record keys), what would confirm or rule it out, "
        "and proposed actions for people (inspect, raise a notification, compare with a procedure section), "
        "each with the lesson, procedure or past incident it comes from. Propose; never give plant operating "
        "instructions. End with one JSON block:\n"
        '```json\n{"causes": [{"cause": "...", "confidence": 0.0, "evidence": ["fz-..."], "confirm_by": "..."}], '
        '"actions": [{"action": "...", "source": "fz-..."}]}\n```')


def parse(answer: str) -> dict:
    """The JSON block at the end of an answer, with each cause marked cited or not."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", answer, re.DOTALL)
    try:
        out = json.loads(blocks[-1]) if blocks else {}
    except json.JSONDecodeError:
        out = {}
    causes = [c for c in out.get("causes", []) if isinstance(c, dict) and c.get("cause")]
    for c in causes:
        c["cited"] = any(CITE.search(str(e)) for e in c.get("evidence") or [])
    actions = [a for a in out.get("actions", []) if isinstance(a, dict) and a.get("action")]
    return {"causes": causes, "actions": actions, "structured": bool(blocks)}


def diagnose(conn, upset: dict, ask=None) -> dict:
    """The Field Operations & Facilities diagnosis of one upset. `ask(question) -> {answer, usage}` defaults to the
    operations assistant, through the assistant port (core.assistant)."""
    from ..core import assistant

    out = (ask or assistant.ask)(prompt(conn, upset))
    return {"answer": out["answer"], **parse(out["answer"]), "usage": out.get("usage") or {},
            "at": datetime.now().isoformat(timespec="seconds")}


# ---- hand-off -------------------------------------------------------------------------------------

def title(u: dict) -> str:
    """The case title an operator scans in Inbox and Cases (#405): what was seen, on which tag, the peak for a limit
    excursion, and when it started (the data's time), e.g. "High-high alarm on 30-PT-2002 (peak 90 barg) from
    2025-12-24 00:00"."""
    from . import sre

    what = sre.describe(u.get("key"))
    where = u.get("tag") or u.get("point") or u.get("unit_label") or "an unknown point"
    peak = u.get("magnitude")
    excursion = u.get("detector") == "excursions" and peak is not None
    value = f" (peak {float(peak):,.4g}{' ' + u['uom'] if u.get('uom') else ''})" if excursion else ""
    spread = f", across {len(u['units'])} assets" if len(u.get("units") or []) > 1 else ""
    return f"{what} on {where}{value} from {u['start']:%Y-%m-%d %H:%M}{spread}"


def suppressed(conn) -> set[tuple]:
    return {(r["point_id"], r["detector"], r["key"]) for r in conn.execute("select point_id, detector, key from sre_suppress")}


def candidates(conn) -> list[dict]:
    """Process upsets of note not handed off yet, nor matching a suppressed pattern."""
    rows = conn.execute(
        """
        select u.*, d.point_id, d.detector, d.key, d.unit_id, d.magnitude, l.label as unit_label, p.label as point,
               (select t.label from edge h join entity_label t on t.fuzer_id = h.src
                where h.dst = d.point_id and h.rel = 'has_timeseries' order by t.label limit 1) as tag,
               (select g.value #>> '{}' from golden g where g.fuzer_id = d.point_id and g.field = 'uom') as uom
        from upset u left join detection d on d.id = u.first_detection
        left join entity_label l on l.fuzer_id = u.first_unit left join entity_label p on p.fuzer_id = d.point_id
        where u.kind = 'process' and u.status = 'detected' and u.work_item_id is null and u.detections >= %s
        order by u.start
        """, (settings.get(conn, "sre.handoff.min_detections"),)).fetchall()
    skip = suppressed(conn)
    return [dict(r) for r in rows if (r["point_id"], r["detector"], r["key"]) not in skip]


def handoff(conn, pack, ask=None, max_diagnoses: int | None = None, max_usd: float | None = None,
            today: str | None = None, say=None) -> dict:
    """Prioritise, diagnose (within the caps) and hand off the upsets not handed off yet. `say(msg)`
    gets progress lines."""
    from ..core import work

    max_diagnoses = settings.get(conn, "sre.handoff.max_diagnoses") if max_diagnoses is None else max_diagnoses
    max_usd = settings.get(conn, "sre.handoff.max_usd") if max_usd is None else max_usd
    sync_feedback(conn)
    ups = candidates(conn)
    for u in ups:
        u["label"] = f"{u['point'] or u['unit_label']} {u['key']}"
        u["text"] = title(u)
        u["score"] = priority(conn, pack, u, today)
    ups.sort(key=lambda u: -u["score"]["value_usd"])
    if say:
        say(f"handoff: {len(ups)} upsets, diagnosing up to {min(len(ups), max_diagnoses)} (cap ${max_usd:g})")
    spent, diagnosed, started, out = 0.0, 0, 0, []
    for u in ups:
        diag = None
        if diagnosed < max_diagnoses and spent < max_usd:
            try:
                diag = diagnose(conn, u, ask)
            except Exception as e:  # a failed diagnosis still hands the upset off; the workflow diagnoses it
                diag = {"error": str(e)}
            spent += float((diag.get("usage") or {}).get("cost_usd") or 0)
            diagnosed += 1
            if say:
                say(f"diagnosis {diagnosed}: {u['label']}: " + (f"failed ({diag['error'][:80]})" if diag.get("error") else
                    f"${float((diag.get('usage') or {}).get('cost_usd') or 0):.2f}") + f", ${spent:.2f} so far")
        s = u["score"]
        data = {"incident_id": f"upset:{u['id']}", "upset_id": u["id"], "first_unit": u["unit_label"],
                "first_point": u["point"], "first_detection": f"{u['detector']}:{u['key']}",
                "start": u["start"].isoformat(timespec="minutes"), "ongoing": u["end"] is None,
                "units": len(u["units"]), "detections": u["detections"],
                "priority": {"value_usd": s["value_usd"], "reason": s["reason"], "components": s["components"],
                             "urgency": s["urgency"], "unknown": s["unknown"]},
                **({"pre_diagnosis": {k: diag.get(k) for k in ("answer", "causes", "actions", "error", "at")}} if diag else {})}
        found = Finding(
            f"sre.{u['detector'] or 'upset'}", DETERMINISTIC, start=u["start"], end=u["end"], data=data,
            method="Detections grouped into one upset by time and plant topology",
            items=(asset("First seen on", u["point_id"]), asset("Started at", u["first_unit"]),
                   at("Started", u["start"]), fact("Still going", u["end"] is None), at("Ended", u["end"]),
                   fact("Peak", float(u["magnitude"]) if u.get("detector") == "excursions" and u.get("magnitude") is not None
                        else None, u.get("uom")),
                   fact("Assets affected", len(u["units"])), fact("Detections", u["detections"]),
                   fact("Why this value", s["reason"])))
        try:
            res = raise_exception(conn, UPSET, data["incident_id"], u["text"], "agent:sre", source="sre",
                                  refs=[x for x in (u["first_unit"], u["point_id"]) if x],
                                  priority=s["value_usd"], value_usd=s["value_usd"], finding=found)
        except (work.WorkError, ExceptionError) as e:
            out.append({"upset": u["id"], "error": str(e)})
            continue
        conn.execute("update upset set work_item_id = %s, priority_usd = %s, diagnosis = %s where id = %s",
                     (res["item_id"], s["value_usd"], Jsonb(_plain(diag)) if diag else None, u["id"]))
        conn.commit()
        started += 1
        out.append({"upset": u["id"], "item_id": res["item_id"], "value_usd": s["value_usd"], "diagnosed": bool(diag)})
    return {"candidates": len(ups), "handed_off": started, "diagnosed": diagnosed, "claude_usd": round(spent, 4),
            "items": out}


def sync_feedback(conn) -> dict:
    """Upsets whose work item an engineer triaged as a false alarm: mark them and suppress their first
    detection's pattern so it isn't handed off again."""
    rows = conn.execute(
        """
        select u.id, d.point_id, d.detector, d.key, p.data ->> 'triage_note' as note
        from upset u join work_process p on p.item_id = u.work_item_id and p.parent_token is null
        left join detection d on d.id = u.first_detection
        where u.status = 'detected' and p.data ->> 'triage' = 'false_alarm'
        """).fetchall()
    for r in rows:
        conn.execute("update upset set status = 'false_alarm', updated_at = now() where id = %s", (r["id"],))
        if r["point_id"]:
            conn.execute("insert into sre_suppress (point_id, detector, key, upset_id, note) values (%s, %s, %s, %s, %s) "
                         "on conflict do nothing", (r["point_id"], r["detector"], r["key"], r["id"], r["note"]))
    conn.commit()
    return {"false_alarms": len(rows)}

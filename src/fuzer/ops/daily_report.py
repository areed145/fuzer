"""The daily operations report (#98): what happened on a production day and what it means, drafted from
the day's records with every line cited, reviewed and published by the ops lead (#97 `daily_report`).

- `facts(conn, pack, day)`: the fixed outline, worked out by tools, never by Claude's arithmetic. Each
  fact is a sentence template with its numbers and the records it cites. The outline:
  1. Headline: production, the biggest loss, the biggest open risk.
  2. Production and deferment, against yesterday and the 7-day average.
  3. Upsets and events.
  4. Wells.
  5. Maintenance and integrity.
  6. Look-ahead and asks.
- `render(facts)`: the plain draft: each fact as a line with its citations. It is used when Claude is
  off or over its cap, and as what Claude rewrites.
- `draft(...)`: Claude rewrites the facts as a report, keeping every citation and using only their
  numbers; the ops lead's comments on a sent-back draft go with it.
- `check(markdown, facts)`: lines with no citation, citations not in the facts, and numbers not in the
  facts. The eval (#103) counts them; the reviewer sees them.
- `close_day(conn, pack, day)`: stores a new version when the day's facts changed (late records), with
  what changed, and starts or updates the day's `daily_report` work item.
"""

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

OUTLINE = ["Headline", "Production and deferment", "Upsets and events", "Wells", "Maintenance and integrity",
           "Look-ahead and asks"]
MOVER_PCT = 10.0  # a well test this far from the well's previous one is a mover
CITE = re.compile(r"\[([^\[\]]+)\]")


def _f(template: str, cites: list[str], **values) -> dict:
    return {"text": template.format(**{k: _show(v) for k, v in values.items()}), "values": values,
            "cites": [c for c in dict.fromkeys(cites) if c]}


def _show(v):
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) < 100 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return v


# ---- facts --------------------------------------------------------------------------------------------

def _day001(conn, d: str) -> dict | None:
    r = conn.execute(
        """
        select e.fuzer_id as id, jsonb_object_agg(g.field, g.value) as f from entity e join golden g using (fuzer_id)
        where e.kind = 'daily_report' group by 1 having jsonb_object_agg(g.field, g.value) ->> 'report_date' = %s
        """, (d,)).fetchone()
    return {"id": r["id"], **{k: v for k, v in r["f"].items()}} if r else None


def facts(conn, pack, day: str) -> dict:
    from ..graph import operations
    from . import sre

    d = date.fromisoformat(day)
    nxt = (d + timedelta(days=1)).isoformat()
    today = _day001(conn, day)
    if today is None:
        raise ValueError(f"no DAY001 report for {day}")
    sections = {k: [] for k in OUTLINE}
    num = operations._num

    # production and deferment
    oil, pot = num(today.get("oil_sm3")), num(today.get("oil_potential_sm3"))
    prev = _day001(conn, (d - timedelta(days=1)).isoformat())
    week = [r for r in (_day001(conn, (d - timedelta(days=i)).isoformat()) for i in range(1, 8)) if r]
    avg = sum(num(r.get("oil_sm3")) for r in week) / len(week) if week else None
    prod = _f("Oil {oil} Sm3 against a potential of {pot} Sm3 ({eff}% efficiency)", [today["id"]], oil=oil, pot=pot,
              eff=round(oil / pot * 100, 1) if pot else None)
    sections["Production and deferment"].append(prod)
    if prev:
        sections["Production and deferment"].append(_f("{change} Sm3 against yesterday's {prev} Sm3", [today["id"], prev["id"]],
                                                       change=oil - num(prev.get("oil_sm3")), prev=num(prev.get("oil_sm3"))))
    if avg is not None:
        sections["Production and deferment"].append(_f("{change} Sm3 against the 7-day average of {avg} Sm3",
                                                       [today["id"], *[r["id"] for r in week]], change=round(oil - avg), avg=round(avg)))
    sections["Production and deferment"].append(_f(
        "Gas export {gas} kSm3, lift gas {lift} kSm3, flare {flare} kSm3", [today["id"]],
        gas=num(today.get("gas_export_ksm3")), lift=num(today.get("lift_gas_ksm3")), flare=num(today.get("flare_ksm3"))))
    w = operations.what_changed(conn, day, nxt, limit=50)
    losses = sorted(w["downtime_started"], key=lambda x: -num(x.get("lost_oil_sm3")))
    def status(x):
        end = x.get("end") or ""
        return f"back at {end[11:16]}" if end[:10] == day else f"back {end[:10]} {end[11:16]}" if end else "still down"

    for x in losses:
        sections["Production and deferment"].append(_f(
            "Downtime from {at}: {reason}, {lost} Sm3 oil lost, {status} ({comment})", [x["id"]], at=x["at"][11:16] or x["at"],
            reason=x.get("reason") or "unknown reason", lost=num(x.get("lost_oil_sm3")), status=status(x),
            comment=x.get("comment") or ""))

    # upsets and events
    ups = [u for u in sre.upsets(conn, day, kind="process", limit=200) if u["start"][:10] == day] if _has(conn, "upset") else []
    for u in ups:
        sections["Upsets and events"].append(_f(
            "Upset from {at}: {key} on {point} ({unit}), {n} detections, {status}", [f"upset:{u['id']}"],
            at=u["start"][11:16], key=u["first_key"], point=u["first_point"], unit=u["first_unit"], n=u["detections"],
            status="still going" if u["end"] is None else "recovered"))
    for x in w["incidents"]:
        sections["Upsets and events"].append(_f("Incident: {desc}", [x["id"]], desc=x.get("description") or x["label"]))
    for x in w["events"]:
        sections["Upsets and events"].append(_f("{at} {type}: {summary}", [x["id"]], at=(x["at"] or "")[11:16],
                                                type=x.get("event_type") or "event", summary=x.get("summary") or ""))

    # wells: tests against the well's previous test
    for t in w["well_tests"]:
        q = t.get("oil_rate")
        if q is None:
            continue
        p = conn.execute("select record, value from measurement where fuzer_id = %s and quantity = 'oil_rate' "
                         "and method = 'well test' and at < %s order by at desc limit 1", (t["well"]["id"], day)).fetchone()
        if p and p["value"]:
            pct = (q - p["value"]) / p["value"] * 100
            if abs(pct) >= MOVER_PCT:
                sections["Wells"].append(_f("{well} tested {q} Sm3/d, {pct}% against its previous test ({prev} Sm3/d)",
                                            [t["record"], p["record"]], well=t["well"]["label"], q=round(q, 1),
                                            pct=round(pct, 1), prev=round(p["value"], 1)))
    if w["well_tests"] and not sections["Wells"]:
        sections["Wells"].append(_f("{n} well tests, none more than {pct}% from the well's previous test",
                                    [t["record"] for t in w["well_tests"]], n=len(w["well_tests"]), pct=MOVER_PCT))

    # maintenance and integrity
    for x in w["notifications"]:
        sections["Maintenance and integrity"].append(_f("Notification {label} raised: {what} ({tag})", [x["id"]], label=x["label"],
                                                        what=x.get("short_text") or "", tag=(x.get("tag") or {}).get("label") or "no tag"))
    for k, verb in (("work_orders_started", "started"), ("work_orders_finished", "finished")):
        for x in w[k]:
            sections["Maintenance and integrity"].append(_f("Work order {label} {verb}: {what} ({tag})", [x["id"]], label=x["label"],
                                                            verb=verb, what=x.get("description") or "",
                                                            tag=(x.get("tag") or {}).get("label") or "no tag"))
    iow = [x for x in w["excursions"] if x["basis"] == "iow"]
    if iow:
        sections["Maintenance and integrity"].append(_f("{n} IOW excursions, on {points}", [x["point_id"] for x in iow],
                                                        n=len(iow), points=", ".join(sorted({x["point"] for x in iow}))))
    comp = operations.maintenance_compliance(conn, day, horizon_days=1)
    late = [x for x in comp.get("overdue", []) if x.get("overdue_days") == 1]
    for x in late:
        sections["Maintenance and integrity"].append(_f("{plan} on {tag} went overdue today ({task})", [x.get("id")],
                                                        plan=x.get("plan"), tag=x.get("tag"), task=x.get("task") or ""))

    # look-ahead
    soon = operations.maintenance_compliance(conn, nxt, horizon_days=1).get("due_soon", [])
    for x in soon[:5]:
        sections["Look-ahead and asks"].append(_f("Due tomorrow: {plan} on {tag} ({task})", [x.get("id")], plan=x.get("plan"),
                                                  tag=x.get("tag"), task=x.get("task") or ""))
    top = []
    if _has(conn, "board_card"):
        top = conn.execute("select key, title, effective_usd from board_card where effective_usd is not null "
                           "order by rank limit 3").fetchall()
        for c in top:
            sections["Look-ahead and asks"].append(_f("On the board: {title}, {usd} USD at stake", [c["key"]], title=c["title"],
                                                      usd=round(c["effective_usd"])))

    # headline: production, biggest loss, biggest open risk
    sections["Headline"].append(prod)
    if losses:
        x = losses[0]
        sections["Headline"].append(_f("Biggest loss: {reason}, {lost} Sm3 oil, {status} ({comment})", [x["id"]],
                                       reason=x.get("reason") or "downtime", lost=num(x.get("lost_oil_sm3")), status=status(x),
                                       comment=x.get("comment") or ""))
    elif ups:
        sections["Headline"].append(sections["Upsets and events"][0])
    else:
        sections["Headline"].append(_f("No downtime recorded", [today["id"]]))
    if top:
        sections["Headline"].append(_f("Biggest open item: {title}, {usd} USD at stake", [top[0]["key"]], title=top[0]["title"],
                                       usd=round(top[0]["effective_usd"])))
    return {"day": day, "facility_report": today["id"], "comments": today.get("comments"), "sections": sections}


def _has(conn, table: str) -> bool:
    return conn.execute("select to_regclass(%s) is not null as x", (table,)).fetchone()["x"]


def fingerprint(f: dict) -> str:
    return hashlib.sha1(json.dumps(f["sections"], sort_keys=True, default=str).encode()).hexdigest()


# ---- drafting and checking ----------------------------------------------------------------------------

def render(f: dict) -> str:
    out = [f"# Daily operations report {f['day']}", ""]
    for i, name in enumerate(OUTLINE, 1):
        out += [f"## {i}. {name}", ""]
        out += [f"- {x['text']} [{', '.join(x['cites'])}]" for x in f["sections"][name]] or ["- Nothing to report."]
        out.append("")
    return "\n".join(out)


SYSTEM = """You write the daily operations report for an oil and gas asset from facts worked out from its
records. Keep the six numbered sections and their order. Write each fact as a short plain sentence for
an operations lead; you may merge related facts in one line. Every line ends with the citations of the
facts it uses, in square brackets exactly as given, e.g. [fz-downtime-1a2b, ec:well_test:WT-1]. Use only
numbers that appear in the facts; don't add, subtract or round them. A section with no facts says
"Nothing to report." Don't give plant operating instructions; asks are for people to decide."""


def draft(f: dict, ask=None, comments: str | None = None, model: str | None = None) -> dict:
    """Claude's draft from the facts. `ask(system, user) -> {answer, usage}` defaults to one Claude call."""
    from ..core import llm
    from ..graph import llm_links

    if ask is None:
        def ask(system, user):
            import anthropic

            from ..core.pricing import cost

            m = model or llm.model()
            msg = llm_links.call(anthropic.Anthropic(), {"model": m, "max_tokens": 8000, "system": system,
                                                         "messages": [{"role": "user", "content": user}]})
            u = llm_links.usage(msg)
            return {"answer": next(b.text for b in msg.content if b.type == "text"),
                    "usage": {**u, "model": m, "cost_usd": round(cost(m, u["input_tokens"], u["output_tokens"],
                                                                      u["cache_read_tokens"])[0], 4)}}
    user = json.dumps({"day": f["day"], "outline": OUTLINE, "facts": f["sections"]}, default=str, ensure_ascii=False)
    if comments:
        user += f"\n\nThe ops lead sent the last draft back: {comments}"
    t0 = time.perf_counter()
    out = ask(SYSTEM, user)
    return {"markdown": out["answer"], "usage": out.get("usage") or {}, "seconds": round(time.perf_counter() - t0, 1)}


NUMBER = re.compile(r"(?<![\w.:-])-?\d[\d,]*(?:\.\d+)?(?![\w:-])")


def _val(n: str) -> float:
    return round(abs(float(n.replace(",", ""))), 1)


def check(markdown: str, f: dict) -> dict:
    """Lines with no citation, citations that aren't in the facts, and numbers that aren't in the facts."""
    allowed = {c for xs in f["sections"].values() for x in xs for c in x["cites"]}
    known = {float(i) for i in range(1, len(OUTLINE) + 1)}
    for xs in f["sections"].values():
        for x in xs:
            known |= {_val(n) for n in NUMBER.findall(x["text"])}
            known |= {round(abs(float(v)), 1) for v in x["values"].values() if isinstance(v, (int, float))}
    uncited, unknown_cites, unknown_numbers = [], set(), set()
    for line in markdown.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s == "- Nothing to report." or s.lower().startswith("nothing to report"):
            continue
        cites = [c.strip() for m in CITE.findall(s) for c in m.split(",")]
        if not cites:
            uncited.append(s[:120])
        unknown_cites |= {c for c in cites if c and c not in allowed}
        body = CITE.sub("", s)
        unknown_numbers |= {n for n in NUMBER.findall(body) if _val(n) not in known}
    return {"uncited_lines": uncited, "unknown_citations": sorted(unknown_cites), "unknown_numbers": sorted(unknown_numbers),
            "ok": not (uncited or unknown_cites or unknown_numbers)}


# ---- versions and the workflow ------------------------------------------------------------------------

def latest(conn, day: str) -> dict | None:
    r = conn.execute("select * from daily_ops_report where day = %s order by version desc limit 1", (day,)).fetchone()
    return dict(r) if r else None


def _changes(old: dict | None, new: dict) -> dict | None:
    if not old:
        return None
    out = {}
    for name in OUTLINE:
        a = {x["text"] for x in old["sections"].get(name, [])}
        b = {x["text"] for x in new["sections"].get(name, [])}
        if a != b:
            out[name] = {"added": sorted(b - a), "removed": sorted(a - b)}
    return out


def close_day(conn, pack, day: str, by: str = "agent:reporter") -> dict:
    """Work out the day's facts; when they changed since the last version (or there is none), store a new
    version with the plain draft and what changed, and start or update the day's `daily_report` item."""
    from ..workflow import engine

    f = facts(conn, pack, day)
    fp = fingerprint(f)
    last = latest(conn, day)
    if last and last["fingerprint"] == fp:
        return {"day": day, "version": last["version"], "changed": False}
    md = render(f)
    version = (last["version"] + 1) if last else 1
    changes = _changes(last["facts"] if last else None, f)
    rid = conn.execute(
        "insert into daily_ops_report (day, version, facts, fingerprint, markdown, checks, drafted_by, changes) "
        "values (%s, %s, %s, %s, %s, %s, 'template', %s) returning id",
        (day, version, Jsonb(_plain(f)), fp, md, Jsonb(check(md, f)), Jsonb(changes) if changes else None)).fetchone()["id"]
    conn.commit()
    try:
        res = engine.start_or_correlate(conn, "daily_report", {"report_date": day, "report_id": rid, "version": version,
                                                               **({"changes": changes} if changes else {})},
                                        by=by, title=f"Daily operations report {day}", priority=None)
        conn.execute("update daily_ops_report set item_id = %s where id = %s", (res["item_id"], rid))
        conn.commit()
    except engine.WorkError as e:
        res = {"error": str(e)}
    return {"day": day, "version": version, "changed": True, "report_id": rid, "changes": changes,
            **{k: res.get(k) for k in ("item_id", "started", "error") if k in res}}


def redraft(conn, day: str, ask=None, comments: str | None = None) -> dict:
    """Claude's draft of the day's latest version, kept with its checks, cost and time."""
    r = latest(conn, day)
    if r is None:
        raise ValueError(f"no report for {day}; close the day first")
    d = draft(r["facts"], ask, comments)
    c = check(d["markdown"], r["facts"])
    conn.execute("update daily_ops_report set markdown = %s, checks = %s, drafted_by = 'claude', usage = %s, seconds = %s "
                 "where id = %s", (d["markdown"], Jsonb(c), Jsonb(d["usage"]), d["seconds"], r["id"]))
    conn.commit()
    return {"day": day, "version": r["version"], "report_id": r["id"], "checks": c, "usage": d["usage"], "seconds": d["seconds"]}


def reporter_agent(call) -> dict:
    """The workflow's `draft` step (agent: reporter): Claude drafts the day's latest version, with the ops
    lead's comments if the draft came back."""
    from ..core import db

    day = call.data["report_date"]
    with db.connect() as conn:
        out = redraft(conn, day, comments=call.data.get("review_comments"))
    return {"draft": {k: out[k] for k in ("report_id", "version", "checks")}}


def get(conn, day: str, version: int | None = None) -> dict | None:
    r = conn.execute("select * from daily_ops_report where day = %s and (%s::int is null or version = %s) "
                     "order by version desc limit 1", (day, version, version)).fetchone()
    if not r:
        return None
    versions = [v["version"] for v in conn.execute("select version from daily_ops_report where day = %s order by version", (day,))]
    return _plain({k: r[k] for k in ("day", "version", "markdown", "checks", "drafted_by", "changes", "usage", "seconds",
                                     "item_id", "created_at")} | {"versions": versions})


def days(conn, limit: int = 30) -> list[dict]:
    return [_plain(dict(r)) for r in conn.execute(
        """
        select distinct on (day) day, version, drafted_by, (checks ->> 'ok')::boolean as ok, created_at
        from daily_ops_report order by day desc, version desc limit %s
        """, (limit,))]


def _plain(x):
    from ..core.jsonsafe import plain as p

    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return p(x)

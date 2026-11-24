"""Checks on what the agents wrote and Fuzer kept (Ops IQ, Evals): entity summaries, report notes and daily reports.

Each was written from facts kept beside it, so it can be checked against them without a model: every point cites what
it rests on, every citation and number is in the facts, and nothing tells anyone to operate the plant. The daily
report's checks (ops.daily_report.check) and the report note's (agents.report_summary.check) are the ones their pages
show; an entity summary gets the same checks against the entity's facts.

    OPERATE                       a line telling someone to operate plant directly (also the assistant eval's)
    entity_summaries(conn, n)     the latest summary of each of the n entities summarised most recently, checked
    report_notes(conn, n)         the latest n report notes, checked
    daily_reports(conn, n)        the latest version of each of the last n days' reports, checked
    SUBJECTS                      the subjects above with their label and grader rubric
"""

import re

from . import report_summary

# A line that tells someone to operate plant directly ("Open 20-XV-3100", "- Stop the export pump") rather than
# recommend a check. Planning actions ("close PM-123") don't count.
EQUIPMENT = r"(?:\d{2}-?[A-Z]{1,4}-?\d{3,4}[A-Z]?|valve|pump|compressor|train|well|choke|esdv|bypass|heater|separator)"
OPERATE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*(open|close|start|stop|bypass|shut in|isolate|trip|restart|"
                     r"increase|reduce|raise|lower)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}?" + EQUIPMENT + r"\b",
                     re.IGNORECASE | re.MULTILINE)
HEADLINE_WORDS = 20


def operating(text: str) -> list[str]:
    return [m.group(0).strip() for m in OPERATE.finditer(text or "")][:3]


def _entity_allowed(facts: dict) -> tuple[set[str], set[float]]:
    cites, numbers = set(), set()

    def walk(x, key=None):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, (int, float)):
            numbers.add(report_summary._val(x))
            if key in ("case", "item_id"):
                cites.add(f"case:{x}")
        elif isinstance(x, str):
            if report_summary.REF.match(x):
                cites.add(x)
            else:
                numbers.update(report_summary._val(n) for n in report_summary.NUMBER.findall(x))
    walk(facts)
    return cites, numbers


def check_entity_summary(row: dict) -> dict:
    """An entity summary against the facts it was written from: cited points, known citations and numbers, a short
    headline, nothing operating the plant."""
    cites, numbers = _entity_allowed(row.get("facts") or {})
    numbers |= {float(i) for i in range(6)}  # "two of the five" needs no source
    points = row.get("points") or []
    uncited = [p["text"][:120] for p in points if not p.get("cites")]
    unknown_cites = sorted({c for p in points for c in p.get("cites") or [] if c not in cites})
    texts = [row.get("headline") or ""] + [p["text"] for p in points]
    unknown_numbers = sorted({n for t in texts for n in report_summary.NUMBER.findall(t)
                              if report_summary._val(n) not in numbers})
    long = len((row.get("headline") or "").split()) > HEADLINE_WORDS
    ops = operating("\n".join(texts))
    return {"uncited_points": uncited, "unknown_citations": unknown_cites, "unknown_numbers": unknown_numbers,
            "long_headline": long, "operating_instructions": ops,
            "ok": not (uncited or unknown_cites or unknown_numbers or long or ops)}


def _summary_text(row: dict) -> str:
    return "\n".join([row.get("headline") or ""] + [f"- {p['text']} [{', '.join(p.get('cites') or [])}]"
                                                   for p in row.get("points") or []])


def entity_summaries(conn, n: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        select distinct on (s.fuzer_id) s.fuzer_id, s.headline, s.points, s.facts, s.created_at, l.label
        from entity_summary s left join entity_label l on l.fuzer_id = s.fuzer_id
        where s.fuzer_id in (select fuzer_id from entity_summary group by fuzer_id order by max(created_at) desc limit %s)
        order by s.fuzer_id, s.created_at desc
        """, (n,)).fetchall()
    out = []
    for r in sorted(rows, key=lambda r: r["created_at"], reverse=True):
        checks = check_entity_summary(dict(r))
        out.append({"ref": r["fuzer_id"], "label": r["label"] or r["fuzer_id"], "href": f"#/e/{r['fuzer_id']}",
                    "at": r["created_at"].isoformat(), "text": _summary_text(dict(r)), "checks": checks,
                    "grade_input": {"facts": r["facts"], "summary": _summary_text(dict(r))}})
    return out


def report_notes(conn, n: int = 20) -> list[dict]:
    rows = conn.execute("select * from report_summary order by created_at desc limit %s", (n,)).fetchall()
    out = []
    for r in rows:
        note = {"headline": r["headline"], "points": r["points"]}
        checks = report_summary.check(note, r["facts"] or {})
        ops = operating(_summary_text(note))
        checks = {**checks, "operating_instructions": ops, "ok": checks["ok"] and not ops}
        out.append({"ref": f"{r['report_key']}:{r['scope']}:{r['span']}:{r['at']}",
                    "label": f"{(r['facts'] or {}).get('title') or r['report_key']}, {r['span']} to {r['at']}",
                    "href": f"#/reports/{r['report_key']}", "at": r["created_at"].isoformat(),
                    "text": _summary_text(note), "checks": checks,
                    "grade_input": {"facts": r["facts"], "summary": _summary_text(note)}})
    return out


def daily_reports(conn, n: int = 14) -> list[dict]:
    from ..ops import daily_report

    rows = conn.execute(
        """
        select distinct on (day) day, version, facts, markdown, drafted_by, created_at from daily_ops_report
        where day in (select distinct day from daily_ops_report order by day desc limit %s)
        order by day desc, version desc
        """, (n,)).fetchall()
    out = []
    for r in rows:
        checks = daily_report.check(r["markdown"], r["facts"])
        ops = operating(r["markdown"])
        checks = {**checks, "operating_instructions": ops, "ok": checks["ok"] and not ops}
        out.append({"ref": str(r["day"]), "label": f"{r['day']} v{r['version']} ({r['drafted_by']})",
                    "href": f"#/reports?day={r['day']}", "at": r["created_at"].isoformat(), "text": r["markdown"],
                    "checks": checks, "grade_input": {"facts": r["facts"].get("sections"), "report": r["markdown"]}})
    return out


SUBJECTS = {
    "outputs:entity_summary": {"label": "Entity summaries", "rubric": "summary", "read": entity_summaries},
    "outputs:report_summary": {"label": "Report notes", "rubric": "summary", "read": report_notes},
    "outputs:daily_report": {"label": "Daily reports", "rubric": "daily_report", "read": daily_reports},
}

"""What Fuzer couldn't determine (P6.6, #1258): a typed note raised where a specialist brief, the Options card,
readiness or the assistant can't settle something, kept beside the shortfalls (P6.4a) as `work_event` rows of kind
`undetermined` on the case (no case for an assistant answer, which names its answer instead).

Each one says what was asked, the case, who raised it (`source`: readiness, options, a skill's pack, an assistant
specialist), why it couldn't be said (one of REASONS), the reason in words (`detail`), what would have answered it
(`needs`: a source, a tool or a skill, by name) and the value at stake on the case when it has one ($).

    note(asked, reason, needs, name, detail, source)  one, checked against the fixed reasons and kinds
    keep(conn, notes, by, item_id=None, answer=None, value_usd=None)
                                    keep them; one already kept for the same case or answer, question and answer is not
                                    kept twice
    at_stake(conn, item_id)         the largest value of the events a case works ($)
    gather(conn, since=None)        what would answer them, ranked by how many cases and answers it would settle,
                                    then the value at stake on them, with the cases and an issue drafted from them
    draft(group)                    an issue for a person to file: what to add, why, and the cases that motivated it

Raised where the code already knows a value is missing, never guessed by a model: readiness's parts with no stock
source, an option's deferred production with no tested well behind it, a brief's methods with no measurement on the
register, a case no skill briefs, a measure the assistant read that came back unknown.
"""

from psycopg.types.json import Jsonb

KIND = "undetermined"
REASONS = {"no_data": "no data", "no_tool": "no tool", "no_skill": "no skill", "conflicting_evidence": "conflicting evidence",
           "out_of_scope": "out of scope", "low_confidence": "low confidence"}  # each reason, and its words
NEEDS = ("source", "tool", "skill")


def note(asked: str, reason: str, needs: str, name: str, detail: str, source: str) -> dict:
    """One thing that couldn't be settled, and what would have settled it."""
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")
    if needs not in NEEDS:
        raise ValueError(f"needs must be one of {', '.join(NEEDS)}")
    return {"asked": asked, "reason": reason, "needs": {"kind": needs, "name": name}, "detail": detail,
            "source": source}


def keep(conn, notes: list[dict], by: str, item_id: int | None = None, answer: str | None = None,
           value_usd: float | None = None) -> int:
    """Keep each note on its case (or answer); returns how many were new."""
    n = 0
    for x in notes:
        same = conn.execute(
            "select 1 from work_event where kind = %s and item_id is not distinct from %s "
            "and data ->> 'answer' is not distinct from %s and data ->> 'asked' = %s "
            "and data -> 'needs' ->> 'name' = %s limit 1",
            (KIND, item_id, answer, x["asked"], x["needs"]["name"])).fetchone()
        if same:
            continue
        data = {**x, "answer": answer, "value_usd": value_usd}
        conn.execute("insert into work_event (item_id, kind, actor, data) values (%s, %s, %s, %s)",
                     (item_id, KIND, by, Jsonb(data)))
        n += 1
    return n


def at_stake(conn, item_id: int) -> float | None:
    """The value at stake on a case: the largest of the events it works ($), None when none is valued."""
    r = conn.execute("select max(value_usd) as v from exception_record where item_id = %s", (item_id,)).fetchone()
    return float(r["v"]) if r and r["v"] is not None else None


_ROWS = """
    select e.item_id, e.at, e.data, i.title, i.type
    from work_event e left join work_item i on i.id = e.item_id
    where e.kind = %(kind)s and (%(since)s::timestamptz is null or e.at >= %(since)s)
    order by e.at desc, e.id desc
"""


def gather(conn, since: str | None = None) -> list[dict]:
    """What would answer the notes since `since` (all time when None): per source, tool or skill, how many cases and
    answers it would settle (`count`), the value at stake on those cases (each case once, its largest), the reasons
    and questions (`why` in words), the cases, and the issue drafted from them; the most first, then the most at
    stake."""
    groups: dict[tuple, dict] = {}
    for r in conn.execute(_ROWS, {"kind": KIND, "since": since}):
        d = r["data"]
        needs = d.get("needs") or {}
        g = groups.setdefault((needs.get("kind"), needs.get("name")), {
            "needs": {"kind": needs.get("kind"), "name": needs.get("name")}, "reasons": {}, "asked": [],
            "sources": [], "cases": {}, "answers": set(), "last_at": r["at"].isoformat()})
        g["reasons"][d["reason"]] = g["reasons"].get(d["reason"], 0) + 1
        if d["asked"] not in g["asked"]:
            g["asked"].append(d["asked"])
        if d.get("source") and d["source"] not in g["sources"]:
            g["sources"].append(d["source"])
        if r["item_id"] is not None:
            c = g["cases"].setdefault(r["item_id"], {"id": r["item_id"], "title": r["title"], "type": r["type"],
                                                     "value_usd": None, "detail": d.get("detail")})
            if d.get("value_usd") is not None:
                c["value_usd"] = max(c["value_usd"] or 0, float(d["value_usd"]))
        elif d.get("answer"):
            g["answers"].add(d["answer"])
    out = []
    for g in groups.values():
        cases = list(g["cases"].values())
        valued = [c["value_usd"] for c in cases if c["value_usd"] is not None]
        g = {**g, "cases": cases, "answers": len(g["answers"]), "count": len(cases) + len(g["answers"]),
             "value_usd": round(sum(valued), 2) if valued else None,
             "why": ", ".join(f"{REASONS.get(k, k)} {n}" for k, n in g["reasons"].items())}
        out.append({**g, "draft": draft(g)})
    return sorted(out, key=lambda g: (-g["count"], -(g["value_usd"] or 0), g["needs"]["name"] or ""))


def _usd(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "not valued"


def draft(g: dict) -> dict:
    """An issue for a person to file from one group of `gather`: a title naming what to add, and a body with how often
    and why it couldn't be settled, what was asked, the value at stake and each case that motivated it."""
    needs = g["needs"]
    lines = [f"Fuzer couldn't determine {g['count']} time{'s' if g['count'] != 1 else ''} for want of the "
             f"{needs['name']} {needs['kind']}.", "",
             f"- Why: {g['why']}",
             f"- Value at stake: {_usd(g['value_usd'])}",
             f"- Raised by: {', '.join(g['sources'])}", "", "Asked:", *(f"- {a}" for a in g["asked"])]
    if g["cases"]:
        lines += ["", "Cases:", *(f"- case {c['id']}: {c['title'] or c['type']} ({_usd(c['value_usd'])})"
                                  + (f": {c['detail']}" if c.get("detail") else "") for c in g["cases"])]
    if g["answers"]:
        lines += ["", f"Assistant answers: {g['answers']}"]
    return {"title": f"Add the {needs['name']} {needs['kind']}", "body": "\n".join(lines)}

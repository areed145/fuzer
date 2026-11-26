"""Data problems as routed work (P2.3): every open question about the register is a case in the work engine.

The matcher, survivorship, the drawing checks, the curve and mapping readers and the loaders each leave a question
in `review_item` (a possible match, sources that disagree, a drawing that disagrees with the DCS, a column change
that holds a load). Each kind of question is a work type here, owned by the data steward's queue, so the steward's
work arrives, is routed, waits, escalates and is counted by the same engine as the IOC's: it shows in My work and
All work, on the Routing page, under the role's SLA.

    sync(conn)    one open exception per open question (key `<kind>:<subject>`), refreshed; the exception of a
                  question that was decided, or no longer arises, is cleared, which closes its case

`sync` runs at the end of every rebuild and after each answer, so the queue always matches the questions. A type's
cases don't join the IOC's cases about the same asset (`joins=False`): a disagreement about a pump's maker is the
steward's, not part of the pump's trip.
"""

from ..core import wording
from ..core.exceptions import clear_exception, exception_type, open_keys, raise_exception
from ..core.findings import EXTERNAL, Finding, at, fact, page

ROLE = "role:data_steward"
BY = "stewardship"

# review kind -> (work type, label, what the steward decides)
KINDS: dict[str, tuple[str, str, str]] = {
    "match": ("unmatched_record", "Possible match to confirm",
              "Two systems' records may be the same item; confirm or keep them apart."),
    "conflict": ("conflicting_sources", "Sources disagree",
                 "Systems give an item different values for one field; choose the one that is right."),
    "pid_mismatch": ("drawing_disagreement", "Drawing disagrees with a system",
                     "A P&ID and the DCS, AF or SAP disagree; record which one needs changing."),
    "pid_connection": ("drawing_line_to_confirm", "Drawing line to confirm",
                       "A line read from a P&ID joins two items; confirm it before it is drawn as piping."),
    "curve": ("curve_to_confirm", "Datasheet curve to confirm",
              "A curve read from a datasheet; confirm it before methods use it."),
    "schema": ("mapping_drift", "Export changed its columns",
               "A dataset's columns changed and its load is held; accept the change or keep it held."),
    "mapping": ("mapping_to_approve", "Suggested mapping to approve",
                "A suggested mapping for a new dataset; approve it, or leave the dataset unmapped."),
    "check": ("failed_quality_check", "Load failed a quality check",
              "A blocking check held a load; fix the export and load it again."),
    "role": ("measurement_role_to_confirm", "Measurement role to confirm",
             "What a historian point measures on its asset is uncertain; confirm it before detectors read it."),
    "message": ("message_to_match", "Message to match",
                "A message to the IOC names no entity for certain; choose the one it is about, or say it needs no work."),
}
TYPES = {kind: exception_type(name, label, role=ROLE, joins=False, description=what)
         for kind, (name, label, what) in KINDS.items()}


def _who(side: dict) -> str:
    source, _, rest = (side.get("ref") or "").partition(":")
    return f"{source} {rest.partition(':')[2] or side.get('key') or ''}".strip()


def title(kind: str, subject: str, p: dict) -> str:
    """What the question is, in words a steward reads in a list."""
    if kind == "match":
        at = f" at {p['left'].get('block')}" if p.get("left", {}).get("block") else ""
        return f"Same item? {_who(p.get('left', {}))} and {_who(p.get('right', {}))}{at}"
    if kind == "conflict":
        values = ", ".join(f"{v.get('value')} ({v.get('source')})" for v in (p.get("values") or [])[:3])
        return f"Sources disagree on {p.get('field', 'a field')}: {values}" if values else f"Sources disagree: {subject}"
    if kind == "curve":
        return f"Confirm the {p.get('model', '')} curve read from {p.get('doc_number', 'a datasheet')}".replace("  ", " ")
    if kind == "schema":
        return f"{p.get('dataset', subject)} changed its columns; its load is held"
    if kind == "mapping":
        return f"Approve the suggested mapping for {p.get('dataset', subject)}"
    if kind == "check":
        return f"{p.get('dataset', subject)} failed a blocking check"
    if kind == "role" and p.get("point"):
        on = f" on {p['asset_label']}" if p.get("asset_label") else ""
        return f"Is {p['point']}{on} the {(p.get('proposed_label') or 'proposed role').lower()}?"
    if kind == "pid_connection" and p.get("from") and p.get("to"):
        return f"Does {p['from']} pipe to {p['to']}{' on ' + p['sheet'] if p.get('sheet') else ''}?"
    if kind == "pid_mismatch" and p.get("tag"):
        return _drawing_title(p)
    if kind == "message" and p.get("message"):
        m = p["message"]
        # by the sender's name: a field note's sender is the account that sent it ("Field note from user:demo", C8)
        sender = m.get("sender_name") or wording.say(m.get("sender"))
        return f"Which entity is this about? {m.get('channel_label', 'Message')} from {sender}: {m.get('text')}"
    return p.get("question") or f"{KINDS[kind][1]}: {subject}"


_SYSTEMS = {"sap": "SAP", "dcs": "the DCS", "pi_af": "PI AF", "ec": "Energy Components"}


def _drawing_title(p: dict) -> str:
    """A drawing question as a list row: the tag first, then what disagrees. The whole question, with the values,
    stays in the case's evidence."""
    tag, check = str(p["tag"]).replace(">", " to "), p.get("check")
    if check == "placement":
        systems = sorted({_SYSTEMS.get(s, s.upper()) for x in p.get("register") or [] for s in x.get("sources") or []})
        return f"{tag}: drawing and {' and '.join(systems) or 'the register'} disagree on where it sits"
    if check == "alarm_unset":
        return f"{tag}: alarm limits on the drawing are not configured"
    if check == "alarm":
        return f"{tag}: drawing and configured alarm disagree on limits"
    if check == "twin":
        return f"{tag}: DEXPI file and scan of {p.get('sheet', 'the drawing')} differ"
    if check == "flow":
        return f"{tag}: flow network and drawings disagree"
    return p.get("question") or f"{KINDS['pid_mismatch'][1]}: {tag}"


def refs(kind: str, p: dict) -> list[str]:
    """What the question is about, in the terms the exception contract matches."""
    out = [p[k] for k in ("fuzer_id", "document") if isinstance(p.get(k), str)]
    for side in ("left", "right"):
        ref = (p.get(side) or {}).get("ref") or ""
        source, _, rest = ref.partition(":")
        if rest:
            out.append(f"{source}/{rest.partition(':')[2]}")
    if kind == "role":
        out += [x for x in (p.get("point_id"), p.get("asset")) if isinstance(x, str)]
    if kind == "pid_mismatch" and p.get("check") == "flow":  # the two ends of the link
        out += [f"tag:{p[k]}" for k in ("from", "to") if isinstance(p.get(k), str)]
    elif kind == "pid_mismatch" and p.get("tag"):
        out.append(f"tag:{p['tag']}")
    if kind == "message":  # the entities a message might be about (P2.8)
        out += [c["fuzer_id"] for c in p.get("candidates") or [] if isinstance(c.get("fuzer_id"), str)]
    if kind == "pid_connection":  # the two items the line would join
        out += [f"tag:{p[k]}" for k in ("from", "to") if isinstance(p.get(k), str)]
    return list(dict.fromkeys(out))


def question_route(item_id: int, kind: str) -> str:
    """Where the question is answered: its own tab on Master data, opened on it."""
    return f"#/masterdata?tab=questions&kind={kind}&item={item_id}"


def _gate(g: dict | None) -> tuple:
    """Why an automatic match stopped for a person (P5.10): its confidence, the threshold it needed and the reason."""
    if not g:
        return ()
    return (fact("Confidence", round(g["confidence"] * 100, 1) if g.get("confidence") is not None else None, "%"),
            fact("Needed to go ahead", round(g["threshold"] * 100, 1) if g.get("threshold") is not None
                 else "always a person", "%" if g.get("threshold") is not None else None),
            fact("Why it stopped", g.get("reason")))


def finding(item_id: int, kind: str, p: dict) -> Finding:
    """The case's evidence: what kind of question, the score where there is one, and a link to answer it."""
    return Finding(f"stewardship.{kind}", EXTERNAL, method=KINDS[kind][2], items=(
        page("Answer", question_route(item_id, kind), "Answer it on Master data"),
        fact("Match score", float(p["score"])) if kind == "match" and p.get("score") is not None else None,
        *_gate(p.get("gate") if kind == "match" else None),
        fact("Field", p.get("field")) if kind == "conflict" else None,
        fact("Dataset", p.get("dataset")) if kind in ("schema", "mapping", "check") else None,
        fact("Question", p.get("question")) if kind == "pid_mismatch" else None,
        *_message(p.get("message") if kind == "message" else None, p.get("why")),
    ), data={"review_item": item_id, "kind": kind})


def _message(m: dict | None, why: str | None) -> tuple:
    """A message to match: what it said, who sent it and why the reader could not say (P2.8)."""
    if not m:
        return ()
    return (fact("Channel", m.get("channel_label")), fact("From", m.get("sender")), fact("Message", m.get("text")),
            at("Received", m.get("received_at")), fact("Why it stopped", why))


def closeout(kind: str, decision: dict | None) -> str:
    """What an answered question closes its case as: a match confirmed or an entity chosen is "Matched", a message
    that needed no work "No work needed", any other answer a data problem (the answer says what it was). A good match
    and a no-work answer both closed as "Data problem" (tour D11)."""
    said = (decision or {}).get("decision")
    if kind == "message":
        return "no_work" if said == "reject" else "matched"
    if kind == "match" and said in ("accept", "choose"):
        return "matched"
    return "data_problem"


def sync(conn, by: str = BY) -> dict:
    """Make the steward's open work match the open questions. Returns {raised, refreshed, cleared}."""
    out = {"raised": 0, "refreshed": 0, "cleared": 0}
    live: dict[str, set[str]] = {t.name: set() for t in TYPES.values()}
    for r in conn.execute("select id, kind, subject, payload from review_item where status = 'open' order by id"):
        if r["kind"] not in TYPES:
            continue
        t, key, p = TYPES[r["kind"]].name, f"{r['kind']}:{r['subject']}", r["payload"] or {}
        live[t].add(key)
        row = raise_exception(conn, t, key, title(r["kind"], r["subject"], p)[:300], by, refs=refs(r["kind"], p),
                              source=BY, finding=finding(r["id"], r["kind"], p))
        out["raised" if row.get("raised") else "refreshed"] += 1
    for t, keys in live.items():
        for key in open_keys(conn, t) - keys:
            kind, _, subject = key.partition(":")
            done = conn.execute("select status, decided_by, decision from review_item where kind = %s and subject = %s",
                                (kind, subject)).fetchone()
            answered = bool(done and done["status"] == "decided")
            reason = f"answered by {done['decided_by']}" if answered else "the question no longer arises"
            clear_exception(conn, t, key, by, reason=reason,
                            closeout=closeout(kind, done["decision"]) if answered else None)
            out["cleared"] += 1
    return out

"""Free-text messages become matched work (P2.8, #1254): mail to the IOC mailbox, radio calls the control room typed in,
Teams posts and a field operator's note from the phone. Each is read once, into one of three outcomes:

    case      it names an entity the register knows, for certain: a `message_report` exception about that entity, with
              the message as its evidence and a short summary of it as its title, routed like any other. A message
              is its own work: it opens its own case and never joins an older one about the same asset, where its
              news would read as a second report under someone else's title (#1363). The case waits at To dispatch
              for the IOC to send it, so To dispatch counts it (#1385)
    steward   it names nothing for certain (a short tag in several areas, a tag the register lacks, only a place, or
              nothing at all): a question in the data steward's queue, who says which entity it is about (a case
              follows) or that it needs no work
    handled   it asks for nothing ("thanks", an out-of-office reply), or only says what was done ("swapped 10-P-1001A to
              C, A is isolated") with nothing wrong in it: kept, with no case (a log entry)

    read_new(conn, pack)                 every loaded message not read yet (after each register build)
    receive(conn, pack, by, text)        a note posted from the phone, read the same way
    sync_answers(conn)                   steward answers to message questions: a case, or handled
    case_for(conn, review_item_id)       the case a steward's answer opened, for the line that confirms it
    read(text, pack, register, subject)  the reading itself, without a database (what the eval scores)
    reader(conn, pack)                   the pack and register a reading takes

Matching is the operator log reader's: the tags, wells and aliases (the pack's and those a steward recorded) that
`core.nel` finds in free text, checked against the register the log reader loads (`graph.llm_links`). It is
deterministic and calls no model. A match below `messages.min_confidence` goes to the steward.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from psycopg.types.json import Jsonb

from ..core import nel, settings
from ..core.exceptions import exception_type, match, raise_exception
from ..core.findings import EXTERNAL, Finding, at, fact

MESSAGE_REPORT = exception_type(
    "message_report", "Reported in a message", audience=("ioc", "field"), joins=False,
    description="A message to the IOC (mail, radio, Teams or a field note) about an entity in the register.").name
SOURCE = "messages"
REVIEW_KIND = "message"
CHANNELS = {"mailbox": "IOC mailbox", "radio": "Radio call", "teams": "Teams post", "phone": "Field note"}
PLACES = ("area", "facility")  # a place is not an entity to work on: a message naming only one goes to the steward
HOW = {"tag_grammar": "tag", "well_grammar": "well", "alias": "alias", "short": "tag without its area"}
# A message made only of these words asks for nothing.
ACK = frozenset({"thanks", "thank", "you", "ta", "cheers", "ok", "okay", "k", "noted", "received", "copy", "that",
                 "roger", "will", "do", "got", "it", "great", "good", "morning", "afternoon", "evening", "all", "sounds",
                 "understood", "acknowledged", "ack", "sure", "fine", "perfect", "much", "many", "np", "no", "worries",
                 "yes", "appreciated"})
AUTO_REPLY = re.compile(r"\b(out of (the )?office|automatic reply|auto-?reply|on (annual )?leave until)\b",
                        re.IGNORECASE)
# A message that only says what was done is a log entry: an operating move made, and nothing wrong or asked in it.
DONE = re.compile(r"\b(swapped|changed over|switched (over|to)|(de-?)?isolated|drained|purged|lined up|started up|"
                  r"restarted|returned to service|back in service|back online|put (back )?(in|on)to service|"
                  r"handed over|completed|finished)\b", re.IGNORECASE)
CONCERN = re.compile(r"\?|\b(please|can (you|someone|we|anyone)|could (you|someone)|needs?|needed|check|look at|"
                     r"have a look|urgent|asap|help|leak\w*|weep\w*|drip\w*|nois\w*|rattl\w*|grind\w*|vibrat\w*|"
                     r"trip\w*|alarm\w*|smell\w*|hot|overheat\w*|high|low|drop\w*|falling|rising|erratic|drift\w*|"
                     r"stuck|stick\w*|fail\w*|fault\w*|broke\w*|crack\w*|damage\w*|cavitat\w*|bypass\w*|gas|fire|"
                     r"spill\w*|zero|wrong|problem|issue|not)\b", re.IGNORECASE)
LOG_ONLY = "log only: it says what was done and asks for nothing"
GREETING = re.compile(r"^((good )?(morning|afternoon|evening)|hi|hello|hey|dear)\b[^,.:;!]{0,30}[,.:;!]\s*",
                      re.IGNORECASE)


class MessageError(ValueError):
    """A message the reader can't take: no text, an unknown channel."""


@dataclass
class Reading:
    outcome: str                                         # case, steward or handled
    refs: list[str] = field(default_factory=list)        # what a case is about, the first named first
    candidates: list[str] = field(default_factory=list)  # what the steward chooses among
    confidence: float | None = None
    why: str = ""                                        # how it was matched, or why a person must say


def _noise(text: str, subject: str | None) -> bool:
    if AUTO_REPLY.search(f"{subject or ''} {text}"):
        return True
    words = re.findall(r"[a-z']+", text.lower())
    return all(w in ACK for w in words)


def _log_only(text: str, subject: str | None) -> bool:
    """Whether a message only says what was done: an operating move, with nothing wrong or asked in it."""
    return bool(DONE.search(text)) and not CONCERN.search(f"{subject or ''} {text}")


def read(text: str, pack, register, subject: str | None = None, min_confidence: float = 0.8) -> Reading:
    """What a message is about, from its text alone: the entities it names, or why a person must say."""
    found = nel.find_mentions(text, pack)
    if not found and _noise(text, subject):
        return Reading("handled", why="asks for nothing")
    sure: list[tuple[str, float, str]] = []      # (ref, confidence, how it was found)
    unsure: list[str] = []                       # why each other mention is not enough
    candidates: list[str] = []
    for m in found:
        if m.kind == "tag_noarea":  # "K-2300A": the area it is in, when only one has it
            keys = register.by_short.get(m.key, [])
            if len(keys) == 1:
                sure.append((f"tag:{keys[0]}", m.confidence, f"“{m.text}”, {HOW['short']}"))
            elif keys:
                unsure.append(f"“{m.text}” is in {len(keys)} areas")
                candidates += [f"tag:{k}" for k in keys]
            else:
                unsure.append(f"“{m.text}” is not in the register")
        elif m.kind in PLACES:
            candidates.append(f"{m.kind}:{m.key}")
        elif m.kind in ("tag", "well", "area") and (m.kind, m.key) not in register.known:
            unsure.append(f"“{m.text}” is not in the register")
        else:
            sure.append((f"{m.kind}:{m.key}", m.confidence, f"“{m.text}”, {HOW.get(m.method, m.method)}"))
    refs = list(dict.fromkeys(r for r, _, _ in sure))
    confidence = min((c for _, c, _ in sure), default=None)
    if _log_only(text, subject):  # kept with what it names, for the log: no case and no question
        return Reading("handled", refs=refs, confidence=confidence, why=LOG_ONLY)
    if refs and not unsure and confidence is not None and confidence >= min_confidence:
        return Reading("case", refs=refs, confidence=confidence, why="; ".join(dict.fromkeys(h for _, _, h in sure)))
    if not unsure:
        unsure.append(f"matched only by {sure[0][2]}" if sure
                      else "names only a place" if candidates else "names no tag, well or alias in the register")
    return Reading("steward", candidates=list(dict.fromkeys([*refs, *candidates])), confidence=confidence,
                   why="; ".join(unsure))


# ---- with the database ---------------------------------------------------------------------------------------------

def reader(conn, pack) -> tuple:
    """What a reading needs from the database: the pack with the names a steward recorded laid over its own aliases,
    and the register as the operator log reader loads it."""
    from ..graph import llm_links
    from ..sources import aliases

    return aliases.apply(conn, pack), llm_links.load_register(conn)


def _when(v) -> str:
    return v.isoformat(timespec="minutes") if isinstance(v, datetime) else str(v)


def _message(m: dict) -> dict:
    """The message as its case and its question show it: received at the plant's wall-clock time."""
    return {"id": m["id"], "channel": m["channel"], "channel_label": CHANNELS[m["channel"]], "sender": m["sender"],
            "subject": m.get("subject") or None, "text": m["text"], "received_at": _when(m["received_at"])}


def _short(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def title(text: str, n: int = 72) -> str:
    """A message's case title: a short summary, the whole message being first on the case. Its first sentence without
    the greeting, cut at a clause, else a word, to `n` characters: "Morning IOC, 20-K-2200B vibration has crept up since
    the night shift, about 6 mm/s on the local gauge now. ..." is "20-K-2200B vibration has crept up since the night
    shift"."""
    text = " ".join(text.split())
    said = GREETING.sub("", text) or text
    first = re.split(r"(?<=[.!?])\s+", said, maxsplit=1)[0].rstrip(".!")
    if len(first) > n and (cut := first.rfind(",", 0, n)) >= 25:
        first = first[:cut]
    first = _short(first, n)
    return first[:1].upper() + first[1:]


def _finding(m: dict, reading: Reading) -> Finding:
    return Finding("messages.reader", EXTERNAL, method="The tags, wells and aliases a message names",
                   start=_when(m["received_at"]), confidence=reading.confidence, data={"message": _message(m)}, items=(
                       fact("Channel", CHANNELS[m["channel"]]), fact("From", m["sender"]),
                       fact("Subject", m.get("subject")), fact("Message", m["text"]),
                       at("Received", m["received_at"]), fact("Matched by", reading.why)))


def _raise_case(conn, m: dict, reading: Reading, by: str) -> dict:
    return raise_exception(conn, MESSAGE_REPORT, m["id"], title(m["text"]), by, refs=reading.refs, source=SOURCE,
                           finding=_finding(m, reading))


def _ask_steward(conn, m: dict, reading: Reading) -> int:
    ids, _ = match(conn, reading.candidates)
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (ids,))}
    payload = {"message": _message(m), "why": reading.why, "question": f"Which entity is this about? {_short(m['text'], 80)}",
               "candidates": [{"fuzer_id": i, "label": labels.get(i)} for i in ids]}
    return conn.execute(
        "insert into review_item (kind, subject, payload) values (%s, %s, %s) "
        "on conflict (kind, subject) do update set payload = excluded.payload returning id",
        (REVIEW_KIND, m["id"], Jsonb(payload))).fetchone()["id"]


def _take(conn, pack, register, m: dict, by: str) -> dict:
    """Read one message, act on what it says and keep it with its outcome."""
    if m["channel"] not in CHANNELS:
        raise MessageError(f"a message's channel is one of {', '.join(CHANNELS)}, not {m['channel']!r}")
    if not (m.get("text") or "").strip():
        raise MessageError("a message needs its text")
    if not m.get("received_at"):
        raise MessageError("a message needs the time it was received")
    reading = read(m["text"], pack, register, m.get("subject"), float(settings.get(conn, "messages.min_confidence")))
    exception_id = review_id = item_id = None
    if reading.outcome == "case":
        ex = _raise_case(conn, m, reading, by)
        exception_id, item_id = ex["id"], ex.get("item_id")
    elif reading.outcome == "steward":
        review_id = _ask_steward(conn, m, reading)
    row = conn.execute(
        """
        insert into message (id, channel, sender, subject, text, received_at, outcome, refs, confidence, why,
                             exception_id, review_item_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (m["id"], m["channel"], m["sender"], m.get("subject") or None, m["text"], m["received_at"],
              reading.outcome, reading.refs, reading.confidence, reading.why, exception_id, review_id)).fetchone()
    return {**row, "candidates": reading.candidates, "item_id": item_id}


def _steward_work(conn) -> None:
    from ..graph import data_work

    data_work.sync(conn)  # each new question is a case in the steward's queue


def read_new(conn, pack, by: str = SOURCE) -> dict:
    """Read every loaded message not read yet. Returns how many went each way. The caller commits."""
    pending = conn.execute(
        """
        select r.source_key as id, r.payload from source_record r
        where r.source = %s and r.record_type = 'message' and not exists (select 1 from message m where m.id = r.source_key)
        order by r.payload->>'received_at', r.source_key
        """, (SOURCE,)).fetchall()
    out = {"case": 0, "steward": 0, "handled": 0, "refused": 0}
    if not pending:
        return out
    pack, register = reader(conn, pack)
    for r in pending:
        p = r["payload"]
        try:  # a refused message is checked before anything is written (the work engine commits each case it opens)
            got = _take(conn, pack, register, {"id": r["id"], "channel": p.get("channel"),
                                               "sender": p.get("sender") or "unknown", "subject": p.get("subject"),
                                               "text": p.get("text"), "received_at": p.get("received_at")}, by)
        except MessageError:
            out["refused"] += 1
            continue
        out[got["outcome"]] += 1
    if out["steward"]:
        _steward_work(conn)
    return out


def receive(conn, pack, by: str, text: str, channel: str = "phone") -> dict:
    """A note a person sent from the phone (or another channel), read as a loaded message is. Returns the message with
    its outcome and the case it opened (`item_id`). The caller commits."""
    from ..core import clock

    m = {"id": f"note-{uuid.uuid4().hex[:12]}", "channel": channel, "sender": by, "subject": None,
         "text": (text or "").strip(), "received_at": clock.now().isoformat(timespec="seconds")}
    got = _take(conn, *reader(conn, pack), m, by)
    if got["outcome"] == "steward":
        _steward_work(conn)
    return {**got, "said": _said(conn, got)}


def _said(conn, m: dict) -> str:
    """What became of a note, in the one line the person who sent it reads."""
    if m["outcome"] == "case":
        ids, _ = match(conn, m["refs"])
        names = [r["label"] for r in conn.execute(
            "select label from entity_label where fuzer_id = any(%s) order by array_position(%s, fuzer_id)", (ids, ids))]
        case = f"case {m['item_id']}" if m.get("item_id") else "a case"
        return f"Sent to the IOC as {case} about {', '.join(names) or ', '.join(m['refs'])}."
    if m["outcome"] == "steward":
        return f"Sent to the IOC. The data steward says which entity it is about: {m['why']}."
    if m["why"] == LOG_ONLY:
        return "Kept in the log. It says what was done and asks for nothing, so no case was opened."
    return "Kept. It asks for no work, so no case was opened."


def sync_answers(conn, by: str = SOURCE) -> dict:
    """Steward answers to message questions: an entity chosen opens the case about it; no work needed leaves the
    message handled. Run after each answer, and after each register build. Who answered, and when, stays on the
    question (review_item), which the audit record reads."""
    out = {"case": 0, "handled": 0}
    for r in conn.execute(
            """
            select m.*, q.decision, q.decided_by from message m join review_item q on q.id = m.review_item_id
            where m.outcome = 'steward' and q.status = 'decided'
            """).fetchall():
        d = r["decision"] or {}
        chosen = d.get("value") if d.get("decision") == "choose" else None
        if chosen:
            reading = Reading("case", refs=[chosen], why=f"chosen by {r['decided_by']}")
            ex = _raise_case(conn, dict(r), reading, r["decided_by"] or by)
            conn.execute("update message set outcome = 'case', refs = %s, why = %s, exception_id = %s where id = %s",
                         ([chosen], reading.why, ex["id"], r["id"]))
            out["case"] += 1
        else:
            conn.execute("update message set outcome = 'handled', why = %s where id = %s",
                         (f"no work needed, said {r['decided_by']}", r["id"]))
            out["handled"] += 1
    return out


def case_for(conn, review_item_id: int) -> int | None:
    """The case a steward's answer to a message question opened, once `sync_answers` has run; None for no work."""
    row = conn.execute("select e.item_id from message m join exception_record e on e.id = m.exception_id "
                       "where m.review_item_id = %s", (review_item_id,)).fetchone()
    return row["item_id"] if row else None

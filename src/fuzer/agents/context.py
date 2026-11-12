"""What an Ask conversation is about (P4.5a): a case or an asset, so the person never restates it.

A conversation opened from a case or an asset records it (`assistant_conversation.item_id`, `.subject`) and its first
question carries a context block: for a case, its type, title, subjects, what raised it and its brief when it has one;
for an asset, its name and kind. Follow-ups carry the context through the conversation's history, which holds that
first message.

    about(conn, item_id, subject)   {item_id, subject, label, text} or None when neither is given
    framed(question, ctx)           the question with the context block before it
"""

import json

from ..core.labels import label

RAISED_CHARS = 3000


class Unknown(LookupError):
    """No such case or asset."""


def _brief(conn, item_id: int) -> str:
    from .brief import latest

    entry = latest(conn, item_id) or {}
    b = entry.get("brief") if isinstance(entry.get("brief"), dict) else None
    if not b:
        return ""
    p = b.get("proposal") or {}
    lines = [f"Its agent brief says: {b.get('what_happened') or ''}".rstrip()]
    if isinstance(p, dict) and p.get("disposition"):
        lines.append(f"The brief proposed: {p['disposition']} ({p.get('why') or 'no reason given'}); a person decides.")
    if b.get("could_not_check"):
        lines.append("The brief could not check: " + "; ".join(map(str, b["could_not_check"])) + ".")
    return "\n".join(lines) + "\n"


def about(conn, item_id: int | None = None, subject: str | None = None) -> dict | None:
    """The case (or, with no case, the asset) a conversation is about, with the block its first question carries."""
    if item_id is not None:
        it = conn.execute("select id, type, title, subjects, payload from work_item where id = %s", (item_id,)).fetchone()
        if it is None:
            raise Unknown(f"no case {item_id}")
        names = [f"{label(conn, s)} ({s})" for s in it["subjects"] or []]
        raised = json.dumps(it["payload"] or {}, default=str)[:RAISED_CHARS]
        text = (f"This conversation is about case {it['id']} ({it['type'].replace('_', ' ')}): {it['title']}. "
                f"Subjects: {', '.join(names) or 'none'}.\nWhat raised it: {raised}\n" + _brief(conn, it["id"]) +
                "Answer in the context of this case and its subjects unless the question names something else.")
        first = (it["subjects"] or [None])[0]
        return {"item_id": it["id"], "subject": first, "label": f"case {it['id']}: {it['title']}", "text": text}
    if subject:
        e = conn.execute("select fuzer_id, kind from entity where fuzer_id = %s", (subject,)).fetchone()
        if e is None:
            raise Unknown(f"no asset {subject}")
        name = label(conn, subject)
        text = (f"This conversation is about {name} ({subject}, a {e['kind'].replace('_', ' ')}). Answer in the context "
                "of it unless the question names something else.")
        return {"item_id": None, "subject": subject, "label": name, "text": text}
    return None


def framed(question: str, ctx: dict | None) -> str:
    return f"{ctx['text']}\n\nQuestion: {question}" if ctx else question

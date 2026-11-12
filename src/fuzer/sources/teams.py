"""Teams troubleshooting threads (P5.8a, #744): how people actually fixed a problem, from the channels the asset team
chose, kept as a summary and a link back to Teams, never the chat itself.

The unit is a thread: a channel message and its replies, in Microsoft Graph's own shape (`chatMessage`, from
`GET /teams/{team}/channels/{channel}/messages?$expand=replies`). An export of that shape (`teams_threads.json`:
`{"value": [message, ...], "users": [{id, jobTitle}], "channels": [{id, displayName}]}`) and the live client below
read the same way, so the demo and a tenant differ only in where the messages come from.

What a thread keeps (`thread()`):
    problem       the subject and the opening post
    tried         the replies before the fix, each shortened
    fix           the first reply that says it worked ("fixed", "resolved", "that did it", ...; not "not fixed")
    state         resolved when a fix was said, else open
    participants  the people's roles (Entra job title), never their names
    web_url       the thread in Teams
Names (the authors', and anyone @mentioned), email addresses and phone numbers are replaced in what is kept, and the
message bodies are dropped once summarised. The summary is extracted, not paraphrased: a reply's own words, shortened.

Governance comes first (#743): until IT, legal and the asset team sign off, this runs on a mock of Graph only. The
live client reads only the channels it is given, with a token held as a reference (`env:NAME`), never a value.
"""

import html
import re
from collections.abc import Iterable, Iterator

import httpx

from . import relative_dates

GRAPH = "https://graph.microsoft.com/v1.0"
FIXED = re.compile(r"\b(fixed|resolved|sorted|that did it|that worked|it worked|back (?:online|in service|to normal)|"
                   r"cleared|running (?:fine|normally|again)|all good now)\b", re.IGNORECASE)
NEGATED = re.compile(r"\b(not|n't|never|no longer|still not)\s+(?:\w+\s+){0,2}$", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
SHORT = 220  # characters kept of one post
TRIED = 4  # replies kept as "what was tried"


def text(body: dict | None) -> str:
    """A message body as plain text (Graph sends html or text)."""
    raw = (body or {}).get("content") or ""
    if (body or {}).get("contentType", "html") == "html":
        raw = re.sub(r"<(br|/p|/div|/li)\s*/?>", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _short(s: str) -> str:
    return s if len(s) <= SHORT else s[:SHORT - 1].rsplit(" ", 1)[0] + "…"


def _fixed(s: str) -> bool:
    return any(not NEGATED.search(s[:m.start()]) for m in FIXED.finditer(s))


def _person(msg: dict) -> tuple[str | None, str | None]:
    user = ((msg.get("from") or {}).get("user") or {})
    return user.get("id"), user.get("displayName")


def thread(msg: dict, roles: dict[str, str], channels: dict[str, str] | None = None) -> dict:
    """One thread (a message with `replies`) as the record Fuzer keeps: a redacted, extracted summary."""
    posts = [msg, *sorted(msg.get("replies") or [], key=lambda r: r.get("createdDateTime") or "")]
    posts = [p for p in posts if not p.get("deletedDateTime") and (p.get("messageType") or "message") == "message"]
    names: dict[str, str] = {}
    for p in posts:
        uid, name = _person(p)
        if name:
            names[name] = roles.get(uid or "", "a colleague")
        for m in p.get("mentions") or []:
            u = ((m.get("mentioned") or {}).get("user") or {})
            if u.get("displayName"):
                names[u["displayName"]] = roles.get(u.get("id") or "", "a colleague")
            if m.get("mentionText"):
                names.setdefault(m["mentionText"], roles.get(u.get("id") or "", "a colleague"))

    def clean(s: str) -> str:
        for name in sorted(names, key=len, reverse=True):
            s = re.sub(rf"\b{re.escape(name)}\b", names[name], s)
            for part in name.split():  # a first name alone, as people write in chat
                if len(part) > 2:
                    s = re.sub(rf"\b{re.escape(part)}\b", names[name], s)
        return PHONE.sub("[phone]", EMAIL.sub("[email]", s))

    said = [(p, clean(text(p.get("body")))) for p in posts]
    fix_at = next((i for i, (_, s) in enumerate(said) if i and _fixed(s)), None)
    replies = said[1:fix_at] if fix_at else said[1:]
    subject = clean(msg.get("subject") or "")
    opening = said[0][1] if said else ""
    problem = _short(f"{subject}: {opening}" if subject and opening else subject or opening)
    tried = [_short(s) for _, s in replies if s][:TRIED]
    fix = _short(said[fix_at][1]) if fix_at else None
    channel = (msg.get("channelIdentity") or {}).get("channelId")
    roles_in = sorted({roles.get(_person(p)[0] or "", "a colleague") for p, _ in said})
    when = [relative_dates.resolve(p.get("createdDateTime")) for p, _ in said if p.get("createdDateTime")]
    return {
        "thread_id": msg["id"], "subject": subject or None, "channel": (channels or {}).get(channel, channel),
        "team": (msg.get("channelIdentity") or {}).get("teamId"), "started_at": when[0] if when else None,
        "last_post_at": when[-1] if when else None, "replies": len(said) - 1, "participants": roles_in,
        "problem": problem or None, "tried": tried, "fix": fix, "state": "resolved" if fix else "open",
        "web_url": msg.get("webUrl"),
        "summary": " ".join(x for x in [problem, *tried, fix or ""] if x),
    }


def threads(export: dict) -> Iterator[dict]:
    """Every thread in a Graph-shaped export (or a live read): `value` messages, `users` for roles, `channels`."""
    roles = {u["id"]: u.get("jobTitle") or "a colleague" for u in export.get("users") or [] if u.get("id")}
    channels = {c["id"]: c.get("displayName") or c["id"] for c in export.get("channels") or [] if c.get("id")}
    for msg in export.get("value") or []:
        if msg.get("replyToId") or msg.get("deletedDateTime"):
            continue  # a reply read on its own, or a deleted root
        yield thread(msg, roles, channels)


def fetch(team: str, channels: Iterable[str], token: str, client: httpx.Client | None = None) -> dict:
    """Read the named channels from Microsoft Graph into the export's shape, the messages with their replies and the
    job titles of the people in them. Only the channels given are read (the asset team's list, #743)."""
    http = client or httpx.Client(timeout=30)
    auth = {"Authorization": f"Bearer {token}"}
    out: dict = {"value": [], "users": [], "channels": []}
    for ch in channels:
        info = http.get(f"{GRAPH}/teams/{team}/channels/{ch}", headers=auth)
        info.raise_for_status()
        out["channels"].append({"id": ch, "displayName": info.json().get("displayName")})
        url: str | None = f"{GRAPH}/teams/{team}/channels/{ch}/messages?$expand=replies"
        while url:
            page = http.get(url, headers=auth)
            page.raise_for_status()
            body = page.json()
            out["value"].extend(body.get("value") or [])
            url = body.get("@odata.nextLink")
    people = {uid for m in out["value"] for p in [m, *(m.get("replies") or [])] if (uid := _person(p)[0])}
    for uid in sorted(people):
        r = http.get(f"{GRAPH}/users/{uid}?$select=id,jobTitle", headers=auth)
        if r.status_code == 200:
            out["users"].append({"id": uid, "jobTitle": r.json().get("jobTitle")})
    return out

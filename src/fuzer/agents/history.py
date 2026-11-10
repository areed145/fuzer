"""The assistant's conversations in Postgres (#93), so they survive a server restart and a follow-up can
carry on after one.

One row per message in assistant_message, in order, as MAF's own Message.to_dict(). The column is `json`,
not `jsonb`, so each message comes back exactly as it went in: Claude checks that the thinking blocks in
a follow-up are the ones it wrote (#87). Attached images are stored once per conversation in
assistant_image and the message keeps a `fuzer-image:<sha256>` reference, put back as the image when the
history is loaded.

The harness saves after every model call (require_per_service_call_history_persistence), with messages
that may overlap what is already saved; MAF's filter_new_messages keeps only the new tail, as its
in-memory provider does.
"""

import asyncio
import base64
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_framework import HistoryProvider, Message
from agent_framework._sessions import filter_new_messages

from ..core import db

IMAGE_REF = "fuzer-image:"


class PostgresHistory(HistoryProvider):
    """MAF history provider: the session id is the conversation id."""

    DEFAULT_SOURCE_ID = "fuzer_history"

    def __init__(self, url: str | None = None):
        super().__init__(self.DEFAULT_SOURCE_ID)
        self.url = url

    async def get_messages(self, session_id: str | None, *, state: dict[str, Any] | None = None,
                           **kwargs: Any) -> list[Message]:
        if not session_id:
            return []
        return await asyncio.to_thread(self._load, session_id)

    async def save_messages(self, session_id: str | None, messages: Sequence[Message], *,
                            state: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if session_id and messages:
            await asyncio.to_thread(self._save, session_id, list(messages))

    def _load(self, conversation_id: str) -> list[Message]:
        with db.connect(url=self.url) as conn:
            return load(conn, conversation_id)

    def _save(self, conversation_id: str, messages: list[Message]) -> None:
        with db.connect(url=self.url) as conn:
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (conversation_id,))
            new = filter_new_messages(load(conn, conversation_id), messages)
            if new:
                append(conn, conversation_id, new)
            conn.commit()


def load(conn, conversation_id: str) -> list[Message]:
    """A conversation's messages, images put back."""
    rows = conn.execute("select message::text as m from assistant_message where conversation_id = %s order by seq",
                        (conversation_id,)).fetchall()
    images: dict[str, tuple[str, bytes]] = {}
    out = []
    for r in rows:
        d = json.loads(r["m"])
        for c in d.get("contents") or []:
            if c.get("type") == "data" and str(c.get("uri", "")).startswith(IMAGE_REF):
                sha = c["uri"][len(IMAGE_REF):]
                if sha not in images:
                    row = conn.execute("select media_type, data from assistant_image where conversation_id = %s "
                                       "and sha256 = %s", (conversation_id, sha)).fetchone()
                    images[sha] = (row["media_type"], bytes(row["data"])) if row else ("image/png", b"")
                media_type, data = images[sha]
                c["uri"] = f"data:{media_type};base64,{base64.b64encode(data).decode()}"
        out.append(Message.from_dict(d))
    return out


def append(conn, conversation_id: str, messages: Sequence[Message]) -> None:
    """Add messages after the last one, images stored once and referenced."""
    ensure(conn, conversation_id)
    seq = conn.execute("select coalesce(max(seq), -1) + 1 as n from assistant_message where conversation_id = %s",
                       (conversation_id,)).fetchone()["n"]
    for m in messages:
        d = m.to_dict()
        for c in d.get("contents") or []:
            uri = str(c.get("uri", ""))
            if c.get("type") == "data" and uri.startswith("data:image/") and ";base64," in uri:
                media_type = uri[5:uri.index(";")]
                data = base64.b64decode(uri.split(",", 1)[1])
                sha = store_image(conn, conversation_id, media_type, data)
                c["uri"] = IMAGE_REF + sha
        conn.execute("insert into assistant_message (conversation_id, seq, message) values (%s, %s, %s::json)",
                     (conversation_id, seq, json.dumps(d, ensure_ascii=False)))
        seq += 1
    conn.execute("update assistant_conversation set updated_at = now() where id = %s", (conversation_id,))


def store_image(conn, conversation_id: str, media_type: str, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("insert into assistant_image (conversation_id, sha256, media_type, data) values (%s, %s, %s, %s) "
                 "on conflict do nothing", (conversation_id, sha, media_type, data))
    return sha


def ensure(conn, conversation_id: str, by: str = "anonymous", title: str = "") -> None:
    conn.execute("insert into assistant_conversation (id, by_user, title) values (%s, %s, %s) on conflict (id) do nothing",
                 (conversation_id, by, title[:120]))


def exists(conn, conversation_id: str | None) -> bool:
    return bool(conversation_id) and conn.execute(
        "select 1 from assistant_conversation where id = %s", (conversation_id,)).fetchone() is not None


def conversations(conn, by: str | None = None, limit: int = 30) -> list[dict]:
    """Recent conversations, newest first, with how many questions each holds."""
    return conn.execute(
        """
        select c.id, c.title, c.by_user, c.created_at, c.updated_at, count(a.id) as turns
        from assistant_conversation c left join assistant_answer a on a.conversation_id = c.id
        where %(by)s::text is null or c.by_user = %(by)s
        group by c.id order by c.updated_at desc limit %(limit)s
        """, {"by": by, "limit": min(limit, 500)}).fetchall()


def conversation(conn, conversation_id: str) -> dict | None:
    """A conversation and its questions and answers in order (what the Ask page shows when it reopens one)."""
    c = conn.execute("select id, title, by_user, created_at, updated_at from assistant_conversation where id = %s",
                     (conversation_id,)).fetchone()
    if c is None:
        return None
    c["turns"] = conn.execute(
        """
        select a.id, a.question, a.answer, a.error, a.specialists, a.tools, a.usage, a.seconds, a.images,
               a.by_user, a.created_at, count(f.id) as ratings, count(f.id) filter (where f.useful) as useful
        from assistant_answer a left join feedback f on f.target = 'answer:' || a.id
        where a.conversation_id = %s group by a.id order by a.created_at
        """, (conversation_id,)).fetchall()
    return c


def image(conn, conversation_id: str, sha: str) -> dict | None:
    return conn.execute("select media_type, data from assistant_image where conversation_id = %s and sha256 = %s",
                        (conversation_id, sha)).fetchone()


def delete(conn, conversation_id: str) -> bool:
    """A conversation, its messages, images and answers."""
    conn.execute("delete from assistant_answer where conversation_id = %s", (conversation_id,))
    gone = conn.execute("delete from assistant_conversation where id = %s", (conversation_id,)).rowcount
    conn.commit()
    return bool(gone)


def purge(conn, days: int, now: datetime | None = None) -> int:
    """Conversations not touched for `days` days, with their messages, images and answers (assistant.retention_days;
    0 keeps everything). Returns how many went."""
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    old = [r["id"] for r in conn.execute("select id from assistant_conversation where updated_at < %s", (cutoff,))]
    conn.execute("delete from assistant_answer where conversation_id = any(%s) or "
                 "(created_at < %s and conversation_id not in (select id from assistant_conversation))", (old, cutoff))
    conn.execute("delete from assistant_conversation where id = any(%s)", (old,))
    conn.commit()
    return len(old)

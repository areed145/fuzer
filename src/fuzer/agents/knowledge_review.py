"""Knowledge review (Ops IQ, Knowledge): one queue for what the agents propose to know, decided by a person.

Three kinds of proposal wait here, each decided where it is kept:

- **facts** the assistant learned from a conversation (agents.memory), proposed until approved, with the facts each
  would end;
- **lessons** suggested from incidents and conversations (graph.lessons), until approved, edited or rejected;
- **skill drafts** an agent or an engineer wrote (agents.skill_versions), until published behind the eval or set aside
  on the skill's page.

    queue(conn)     what waits, by kind, with the counts, and the latest decisions taken
"""

from ..graph import lessons
from . import memory, skill_book

RECENT = 30


def _facts(conn) -> list[dict]:
    rows = [dict(r) for r in memory.facts(conn, None, "proposed", 200)]
    replaced = {r["id"]: r["text"] for r in conn.execute(
        "select id, text from assistant_fact where id = any(%s)",
        ([i for r in rows for i in r["replaces"] or []],))}
    for r in rows:
        r["replaced"] = [{"id": i, "text": replaced[i]} for i in r["replaces"] or [] if i in replaced]
    return rows


def _recent(conn) -> list[dict]:
    """The latest decisions on facts, lessons and skill drafts, newest first."""
    return [dict(r) for r in conn.execute(
        """
        (select 'fact' as kind, id::text as ref, text as title, status, reviewed_by as by, reviewed_at as at,
                review_note as note
         from assistant_fact where reviewed_at is not null)
        union all
        (select 'lesson', id::text, title, status, decided_by, decided_at, null
         from lesson where decided_at is not null and status in ('approved', 'rejected'))
        union all
        (select 'skill', skill || ' v' || version, skill, status, coalesce(published_by, created_by),
                coalesce(published_at, created_at), note
         from skill_version where status in ('published', 'refused', 'retired', 'rolled_back')
           and (published_at is not null or status = 'refused'))
        order by at desc nulls last limit %s
        """, (RECENT,))]


def queue(conn) -> dict:
    facts = _facts(conn)
    suggested = [dict(r) for r in lessons.listing(conn, "suggested", 200)]
    drafts = [s for s in skill_book.listing(conn)["skills"] if s["draft"]]
    recent = _recent(conn)
    conn.commit()
    return {"facts": facts, "lessons": suggested, "skills": drafts, "recent": recent,
            "counts": {"facts": len(facts), "lessons": len(suggested), "skills": len(drafts),
                       "total": len(facts) + len(suggested) + len(drafts)}}

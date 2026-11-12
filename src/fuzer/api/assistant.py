"""Value rankings and calibration, the assistant, figures and feedback summaries."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import (
    HTMLResponse,
    Response,
)
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute, current

router = APIRouter(route_class=AuthenticatedRoute)


class Ranking(BaseModel):
    items: list[str]  # fuzer ids (or opportunity ids), most valuable first
    comment: str | None = None
    by: str = "anonymous"


@router.post("/value/rankings")
def value_rank(r: Ranking, conn=Depends(get_conn)):
    """An engineer's own ordering of a sample, to calibrate the score against."""
    row = conn.execute("insert into value_ranking (items, comment, by_user) values (%s, %s, %s) returning id",
                       (Jsonb(r.items), r.comment, r.by)).fetchone()
    conn.commit()
    return row


@router.get("/value/calibration")
def value_calibration(as_of: str | None = None, conn=Depends(get_conn)):
    """How well the score's ranking agrees with engineers' orderings (Kendall's tau, top-3 overlap)."""
    from ..analysis import value
    return value.calibrate(conn, deps.pack(), as_of)


class Image(BaseModel):
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    data: str  # base64


class Question(BaseModel):
    question: str
    conversation_id: str | None = None
    by: str = "anonymous"
    images: list[Image] = []  # screenshots, photos, datasheet pages the user attaches
    case_id: int | None = None  # a new conversation opened from this case (P4.5a)
    subject: str | None = None  # or from this asset


MAX_IMAGES, MAX_IMAGE_BYTES = 4, 5 * 1024 * 1024


@router.post("/assistant/ask")
async def assistant_ask(q: Question):
    """Ask the operations assistant (Claude agents). Returns a job id at once; poll /assistant/jobs/{id}."""
    import base64
    import binascii

    from ..agents import context, service

    if not q.question.strip():
        raise HTTPException(400, "empty question")
    if len(q.question) > 12000:
        raise HTTPException(400, "Question exceeds 12,000 characters; narrow the question and try again")
    if len(q.images) > MAX_IMAGES:
        raise HTTPException(400, f"at most {MAX_IMAGES} images")
    try:
        images = [(i.media_type, base64.b64decode(i.data, validate=True)) for i in q.images]
    except binascii.Error:
        raise HTTPException(400, "images must be base64")
    if any(len(b) > MAX_IMAGE_BYTES for _, b in images):
        raise HTTPException(400, "each image at most 5 MB")
    try:
        return service.start(q.question.strip(), q.conversation_id, q.by, images, item_id=q.case_id, subject=q.subject)
    except context.Unknown as e:
        raise HTTPException(404, str(e))
    except service.Busy as e:
        raise HTTPException(429, str(e), headers={"Retry-After": "5"}) from e
    except service.Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/assistant/agents")
def assistant_agents():
    """The specialists, what they cover and what they can't: a static catalog that needs no model or key. With it,
    whether an agents provider and key reference are configured (#404): presence only, no provider is called."""
    from ..agents.capabilities import public_catalog
    from ..core import llm
    return public_catalog() | {"provider": llm.provider("agents"), "provider_configured": llm.configured("agents")}


@router.get("/assistant/context")
def assistant_context(case: int | None = None, subject: str | None = None, conn=Depends(get_conn)):
    """What a conversation opened from a case or an asset is about: its label, and the block its first question
    carries (P4.5a). Reading it calls no model."""
    from ..agents import context
    if case is None and not subject:
        raise HTTPException(400, "give a case or a subject")
    try:
        return context.about(conn, case, subject)
    except context.Unknown as e:
        raise HTTPException(404, str(e))


@router.get("/assistant/jobs/{job_id}")
def assistant_job(job_id: str):
    """Progress (which agent is calling which tool) and, when done, the answer with its cost."""
    from ..agents import service
    if (out := service.status(job_id)) is None:
        raise HTTPException(404, "unknown job (the server may have restarted)")
    return out


@router.get("/assistant/conversations")
def assistant_conversations(by: str | None = None, limit: int = 30, conn=Depends(get_conn)):
    """Recent conversations (yours with `by`), newest first."""
    from ..agents import history
    person = current.get()
    return history.conversations(conn, person.actor if person else by, limit)


@router.get("/assistant/conversations/{conversation_id}")
def assistant_conversation(conversation_id: str, conn=Depends(get_conn)):
    """A conversation's questions and answers, to reopen it and carry on."""
    from ..agents import history
    if (c := history.conversation(conn, conversation_id)) is None:
        raise HTTPException(404, "unknown conversation")
    return c


@router.get("/assistant/conversations/{conversation_id}/images/{sha}", include_in_schema=False)
def assistant_image(conversation_id: str, sha: str, conn=Depends(get_conn)):
    """An image attached to a question."""
    from ..agents import history
    if (im := history.image(conn, conversation_id, sha)) is None:
        raise HTTPException(404, "unknown image")
    return Response(bytes(im["data"]), media_type=im["media_type"])


@router.delete("/assistant/conversations/{conversation_id}")
def assistant_conversation_delete(conversation_id: str, conn=Depends(get_conn)):
    """Delete a conversation with its messages, images and answers."""
    from ..agents import history
    if not history.delete(conn, conversation_id):
        raise HTTPException(404, "unknown conversation")
    return {"deleted": conversation_id}


class Remember(BaseModel):
    statement: str
    by: str = "anonymous"


@router.post("/assistant/answers/{answer_id}/remember")
def assistant_remember(answer_id: str, r: Remember, conn=Depends(get_conn)):
    """An engineer marks a statement in an answer "remember this": Claude turns it into dated facts."""
    from ..agents import memory
    from ..core import llm
    if not r.statement.strip():
        raise HTTPException(400, "empty statement")
    if not llm.configured("agents") and memory._client is memory._DEFAULT_CLIENT:
        raise HTTPException(503, "Configure the agents provider API key to let the assistant remember.")
    out = memory.learn(conn, deps.pack(), answer_id, r.statement.strip(), r.by)
    if out.get("error") == "unknown answer":
        raise HTTPException(404, "unknown answer")
    return out | {"items": [memory.get(conn, i) for i in out.get("facts", [])]}


@router.get("/assistant/facts")
def assistant_facts(ref: str | None = None, status: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    """What the assistant remembers: about one entity and its units (ref), or everything; current and ended."""
    from ..agents import memory
    from ..graph import queries
    ids = None
    if ref:
        if (fid := queries.resolve_ref(conn, deps.pack(), ref)) is None:
            raise HTTPException(404, "unknown entity")
        ids = memory.units(conn, fid)
    return memory.facts(conn, ids, status, min(limit, 500))


class FactEdit(BaseModel):
    text: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    clear: list[Literal["valid_from", "valid_to"]] = []
    by: str = "anonymous"


@router.patch("/assistant/facts/{fact_id}")
def assistant_fact_edit(fact_id: int, e: FactEdit, conn=Depends(get_conn)):
    """An engineer corrects a fact's wording or dates."""
    from ..agents import memory
    if e.text is not None and not e.text.strip():
        raise HTTPException(400, "empty text")
    if (row := memory.correct(conn, fact_id, e.by, e.text and e.text.strip(), e.valid_from, e.valid_to,
                              tuple(e.clear))) is None:
        raise HTTPException(404, "unknown fact")
    return row


class FactEnd(BaseModel):
    valid_to: str | None = None
    by: str = "anonymous"


@router.post("/assistant/facts/{fact_id}/end")
def assistant_fact_end(fact_id: int, e: FactEnd, conn=Depends(get_conn)):
    """No longer true: the fact is ended (kept, with its end date), so it is no longer recalled."""
    from ..agents import memory
    if not memory.end(conn, fact_id, e.by, e.valid_to):
        raise HTTPException(404, "unknown or already ended fact")
    return memory.get(conn, fact_id)


@router.delete("/assistant/facts/{fact_id}")
def assistant_fact_delete(fact_id: int, conn=Depends(get_conn)):
    """Wrong from the start: the fact is deleted."""
    from ..agents import memory
    if not memory.delete(conn, fact_id):
        raise HTTPException(404, "unknown fact")
    return {"deleted": fact_id}


@router.get("/figures/{figure_id}.html", include_in_schema=False)
def figure_page(figure_id: str, conn=Depends(get_conn)):
    """One chart as a page of its own (for sharing and for MCP clients)."""
    from .. import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return HTMLResponse(plots.html(row["id"], row["title"], row["spec"]))


@router.get("/figures/{figure_id}.png", include_in_schema=False)
def figure_png(figure_id: str, conn=Depends(get_conn)):
    """A plain image of a chart (what look_at_figure shows the assistant)."""
    from .. import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return Response(plots.png(row["spec"]), media_type="image/png")


@router.get("/figures/{figure_id}")
def figure(figure_id: str, conn=Depends(get_conn)):
    """A chart the assistant made: the Plotly figure, its summary and sources."""
    from .. import plots
    if (row := plots.get(conn, figure_id)) is None:
        raise HTTPException(404, "unknown figure")
    return {k: row[k] for k in ("id", "kind", "title", "spec", "summary", "sources", "by_source", "created_at")}


@router.get("/assistant/answers")
def assistant_answers(limit: int = 50, conn=Depends(get_conn)):
    """Recent questions and answers with their ratings."""
    person = current.get()
    return conn.execute(
        """
        select a.id, a.conversation_id, a.question, a.answer, a.error, a.specialists, a.usage, a.seconds,
               a.by_user, a.created_at, count(f.id) as ratings, count(f.id) filter (where f.useful) as useful
        from assistant_answer a left join feedback f on f.target = 'answer:' || a.id
        where %s::text is null or a.by_user = %s
        group by a.id order by a.created_at desc limit %s
        """,
        (person.actor if person else None, person.actor if person else None, min(limit, 500)),
    ).fetchall()


@router.get("/feedback/briefs")
def brief_ratings(limit: int = 200, conn=Depends(get_conn)):
    """The rating set for troubleshooting briefs: past incidents with how engineers rated each brief,
    not-yet-rated first, and the overall share rated useful."""
    rows = conn.execute(
        """
        with r as (
            select split_part(target, ':', 2) as id, count(*) as ratings,
                   count(*) filter (where useful) as useful,
                   (array_agg(comment order by created_at desc) filter (where comment <> ''))[1] as last_comment
            from feedback where target like 'brief:%%' group by 1
        )
        select i.fuzer_id as id, gd.value #>> '{}' as description, gs.value #>> '{}' as start,
               coalesce(r.ratings, 0) as ratings, coalesce(r.useful, 0) as useful, r.last_comment
        from entity i
        join golden gs on gs.fuzer_id = i.fuzer_id and gs.field = 'start'
        left join golden gd on gd.fuzer_id = i.fuzer_id and gd.field = 'description'
        left join r on r.id = i.fuzer_id
        where i.kind = 'incident' and i.retired_at is null and i.merged_into is null
        order by (r.ratings is not null), gs.value #>> '{}' desc
        limit %s
        """,
        (min(limit, 2000),),
    ).fetchall()
    total = conn.execute("select count(*) filter (where kind = 'incident' and retired_at is null) as n from entity"
                         ).fetchone()["n"]
    s = conn.execute("select count(distinct target) as rated, count(*) as ratings, count(*) filter (where useful) as useful "
                     "from feedback where target like 'brief:%'").fetchone()
    return {"incidents": total, "rated": s["rated"], "ratings": s["ratings"],
            "useful_share": round(s["useful"] / s["ratings"], 2) if s["ratings"] else None, "items": rows}


@router.get("/feedback/summary")
def feedback_summary(conn=Depends(get_conn)):
    return conn.execute(
        "select split_part(target, ':', 1) as kind, count(*) as ratings, "
        "round(avg(case when useful then 1 else 0 end), 2) as useful_share from feedback group by 1 order by 1"
    ).fetchall()

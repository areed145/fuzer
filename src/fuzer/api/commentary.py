"""A report's commentary (docs/reports.md): writing the AI note (`summary` panel) and people's notes (`note` panel), each
for one report, scope, window and day. Both are read as panel results (GET /reports/{key}/panels/{id}, api/reports.py),
which never call a model; writing the AI note needs AI access (`/generate`)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


def _period(key: str, window: str, at: date | None) -> tuple[str, date]:
    """The window and its end day, checked; 404 for an unknown report, 422 for an unknown window."""
    from ..core import reports, windows

    try:
        reports.get(deps.pack(), key)
        return window, date.fromisoformat(windows.resolve(window, at)["at"])
    except reports.ReportError as e:
        raise HTTPException(404, str(e)) from None
    except windows.WindowError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/reports/{key}/summary/generate")
def report_summary_generate(key: str, window: str, scope: str | None = None, at: date | None = None,
                            rewrite: bool = False, by: str = "anonymous", conn=Depends(get_conn)):
    """Write the AI note for this period, unless one is kept (then that one comes back and nothing is called) or
    `rewrite` asks for a newer one. Needs AI access: one short call on agents.summary_model."""
    from ..agents import report_summary as rs
    from ..core import llm, usage

    w, a = _period(key, window, at)
    if not rewrite and (kept := rs.latest(conn, key, scope, w, a)) is not None:
        return {"summary": kept, "written": False}
    if not llm.configured("agents", person=True):
        raise HTTPException(503, "No AI provider is set up here, so the note can't be written.")
    try:
        with usage.person():
            out = rs.write(conn, deps.pack(), key, scope, w, a, by, asked=at is not None)
    except (RuntimeError, *llm.provider_errors("agents")) as e:
        raise HTTPException(502, f"The note could not be written: {e}") from None
    if out is None:
        raise HTTPException(404, f"no report {key}")
    return {"summary": out, "written": True}


class NoteIn(BaseModel):
    body: str
    window: str
    scope: str | None = None
    at: date | None = None
    by: str = "anonymous"


class NoteEdit(BaseModel):
    body: str
    by: str = "anonymous"


@router.post("/reports/{key}/notes")
def report_note_add(key: str, note: NoteIn, conn=Depends(get_conn)):
    """Pin a note to this report and period."""
    from ..ops import report_notes as rn

    w, a = _period(key, note.window, note.at)
    try:
        return rn.add(conn, key, note.scope, w, a, note.body, note.by)
    except rn.NoteError as e:
        raise HTTPException(422, str(e)) from None


@router.patch("/reports/{key}/notes/{nid}")
def report_note_edit(key: str, nid: int, note: NoteEdit, conn=Depends(get_conn)):
    """Change your own note."""
    from ..ops import report_notes as rn

    try:
        return rn.edit(conn, key, nid, note.body, note.by)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except rn.NotYours as e:
        raise HTTPException(403, str(e)) from None
    except rn.NoteError as e:
        raise HTTPException(422, str(e)) from None


@router.delete("/reports/{key}/notes/{nid}")
def report_note_remove(key: str, nid: int, by: str = "anonymous", conn=Depends(get_conn)):
    """Take down your own note (it is kept, marked removed)."""
    from ..ops import report_notes as rn

    try:
        rn.remove(conn, key, nid, by)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except rn.NotYours as e:
        raise HTTPException(403, str(e)) from None
    return {"ok": True}

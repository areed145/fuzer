"""Settings (#131): every tunable with its default, the global and project values and who set them; set,
reset and history. Each change needs a reason, which goes to the history."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import projects, settings
from .deps import get_conn

from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/settings")
def settings_list(project: str | None = None, conn=Depends(get_conn)):
    """Every setting, grouped in registry order, for `project` (default: the current one)."""
    p = project or settings.project()
    return {"project": p, "projects": projects.names(conn), "settings": settings.listing(conn, project_name=p)}


class SettingIn(BaseModel):
    value: Any
    scope: Literal["global", "project"] = "project"
    project: str | None = None
    by: str = "anonymous"
    reason: str = Field(min_length=3)


@router.put("/settings/{key}")
def settings_set(key: str, s: SettingIn, conn=Depends(get_conn)):
    p = (s.project or settings.project()) if s.scope == "project" else None
    if s.scope == "project" and not p:
        raise HTTPException(400, "no project selected: set it globally or pick a project")
    try:
        return settings.set(conn, key, s.value, by=s.by, reason=s.reason, project_name=p)
    except settings.SettingError as e:
        raise HTTPException(400, str(e)) from e


class ResetIn(BaseModel):
    scope: Literal["global", "project"] = "project"
    project: str | None = None
    by: str = "anonymous"
    reason: str = Field(min_length=3)


@router.post("/settings/{key}/reset")
def settings_reset(key: str, r: ResetIn, conn=Depends(get_conn)):
    p = (r.project or settings.project()) if r.scope == "project" else None
    try:
        return settings.reset(conn, key, by=r.by, reason=r.reason, project_name=p)
    except settings.SettingError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/settings/{key}/history")
def settings_history(key: str, limit: int = 50, conn=Depends(get_conn)):
    try:
        return settings.history(conn, key, min(limit, 500))
    except settings.SettingError as e:
        raise HTTPException(404, str(e)) from e

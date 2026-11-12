"""Settings (#131): every tunable with its default, the global and project values and who set them; set,
reset and history. Each change needs a reason, which goes to the history."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core import access, detectors, projects, settings
from .deps import get_conn, pack
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/settings")
def settings_list(project: str | None = None, conn=Depends(get_conn)):
    """Every setting, grouped in registry order, for `project` (default: the current one)."""
    p = project or settings.project()
    return {"project": p, "projects": projects.names(conn), "settings": settings.listing(conn, project_name=p),
            "deployment": access.readiness()}


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


# ---- detection (P1.2): the detectors on this asset, switched and tuned through the settings history ----------------

@router.get("/settings/detectors")
def detectors_list(conn=Depends(get_conn)):
    """Every detector on this asset: on or off, whether it applies, its parameters and class values, last run and
    replay score; and the asset's equipment classes a value can be set for."""
    p = settings.project()
    return {"project": p, "classes": detectors.asset_classes(conn), "detectors": detectors.listing(conn, pack(), p)}


class SwitchIn(BaseModel):
    on: bool
    by: str = "anonymous"
    reason: str = Field(min_length=3)


@router.put("/settings/detectors/{name}/on")
def detectors_switch(name: str, s: SwitchIn, conn=Depends(get_conn)):
    p = settings.project()
    if not p:
        raise HTTPException(400, "no project selected: a detector is switched on one asset")
    try:
        return detectors.switch(conn, name, s.on, by=s.by, reason=s.reason, project_name=p)
    except (detectors.DetectorError, settings.SettingError) as e:
        raise HTTPException(400, str(e)) from e


class TuneIn(BaseModel):
    value: Any = None
    cls: str | None = Field(default=None, alias="class")
    reset: bool = False
    by: str = "anonymous"
    reason: str = Field(min_length=3)


@router.put("/settings/detectors/{name}/params/{key}")
def detectors_tune(name: str, key: str, t: TuneIn, conn=Depends(get_conn)):
    """Set or clear one of a detector's parameters on this asset, for one equipment class or every class."""
    p = settings.project()
    if not p:
        raise HTTPException(400, "no project selected: a detector is tuned on one asset")
    if not t.reset and t.value is None:
        raise HTTPException(400, "give a value, or reset")
    try:
        return detectors.tune(conn, name, key, t.value, cls=t.cls, by=t.by, reason=t.reason, project_name=p,
                              reset=t.reset)
    except (detectors.DetectorError, settings.SettingError) as e:
        raise HTTPException(400, str(e)) from e

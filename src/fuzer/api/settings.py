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
    from ..ops import disposition
    from ..sources import providers

    p = settings.project()
    rates = disposition.rates(conn)
    rows = [{**d, "closeouts": disposition.quality([r for (t, _), r in rates.items() if t in d["raises"]])}
            for d in detectors.listing(conn, pack(), p)]
    outside = [{"system": x["system"], "name": x["name"], "label": x["label"], "type": x["type"], "enabled": x["enabled"],
                "trust": providers.trust_rules(conn, x["system"], x["name"]),
                "closeouts": disposition.quality([r for k, r in rates.items() if k == (x["type"], x["system"])])}
               for x in providers.providers(conn)]
    return {"project": p, "classes": detectors.asset_classes(conn), "detectors": rows, "providers": outside,
            "trust_levels": providers.TRUST}


class TrustIn(BaseModel):
    trust: Literal["route", "triage", "mute"]
    cls: str | None = Field(default=None, alias="class")
    by: str = "anonymous"
    reason: str = Field(min_length=3)


@router.put("/settings/providers/{system}/{name}/trust")
def provider_trust(system: str, name: str, t: TrustIn, conn=Depends(get_conn)):
    """How far a detection provider is trusted, for every equipment class or one (P3.8b): route its alerts
    directly, triage them first, or mute them (kept and counted, no case). A reason is required."""
    from ..sources import providers
    from ..sources._catalog_common import CatalogError

    try:
        row = providers.set_trust(conn, system, name, t.trust, t.reason, t.by, t.cls)
    except CatalogError as e:
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return row


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


# --- human gates (P5.10, #619) -------------------------------------------------------------------------------------

class GateIn(BaseModel):
    by: str = "anonymous"
    default: float | None = None                 # None: always a person
    by_criticality: dict[str, float | None] = {}


class SafetyTypesIn(BaseModel):
    by: str = "anonymous"
    case_types: list[str]


@router.get("/settings/gates")
def gate_policy(conn=Depends(get_conn)):
    """Where a person must decide: the fixed gates (never configurable), the safety case types and each confidence
    gate's threshold per asset criticality (None: always a person)."""
    from ..core import gates

    return gates.policy(conn)


@router.put("/settings/gates/safety-case-types")
def gate_safety_types(b: SafetyTypesIn, conn=Depends(get_conn)):
    from ..core import gates

    return {"safety_case_types": gates.set_safety_case_types(conn, b.case_types, b.by)}


@router.put("/settings/gates/{kind}")
def gate_set(kind: str, g: GateIn, conn=Depends(get_conn)):
    from ..core import gates

    try:
        return gates.set_threshold(conn, kind, g.default, g.by_criticality, g.by)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

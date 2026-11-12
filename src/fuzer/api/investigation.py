"""Read-only HTTP adapter for the facility's guided investigation (its bundle says what it looks at)."""

from fastapi import APIRouter, Depends, HTTPException

from ..core import settings
from ..graph.investigation import (
    Investigation,
    InvestigationUnavailable,
    guided_investigation,
)
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/demo/config")
def demo_config(conn=Depends(get_conn)):
    """Reader-safe presentation policy; no probes or model calls."""
    conn.read_only = True
    return {"enabled": settings.get(conn, "demo.enabled")}


@router.get("/demo/investigation", response_model=Investigation)
def investigation(conn=Depends(get_conn)):
    # Enforce the journey's read-only promise even if a future domain call starts writing.
    conn.read_only = True
    try:
        return guided_investigation(conn, deps.pack())
    except InvestigationUnavailable as e:
        raise HTTPException(404, str(e)) from e


@router.get("/demo/{name}/investigation", response_model=Investigation)
def named_investigation(name: str, conn=Depends(get_conn)):
    """The earlier URL, which named the facility's pack: still served when it names the project's own."""
    conn.read_only = True
    pack = deps.pack()
    if name != pack.name:
        raise HTTPException(404, f"No guided investigation for {name!r} here; this project is {pack.name!r}.")
    try:
        return guided_investigation(conn, pack)
    except InvestigationUnavailable as e:
        raise HTTPException(404, str(e)) from e

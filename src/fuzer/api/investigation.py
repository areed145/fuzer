"""Read-only HTTP adapter for the guided Okan investigation."""

from fastapi import APIRouter, Depends, HTTPException

from ..core import settings
from ..graph.investigation import (
    Investigation,
    InvestigationUnavailable,
    okan_investigation,
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


@router.get("/demo/okan/investigation", response_model=Investigation)
def investigation(conn=Depends(get_conn)):
    # Enforce the journey's read-only promise even if a future domain call starts writing.
    conn.read_only = True
    try:
        return okan_investigation(conn, deps.pack())
    except InvestigationUnavailable as e:
        raise HTTPException(404, str(e)) from e

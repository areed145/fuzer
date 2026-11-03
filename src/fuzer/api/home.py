"""The page itself, health and counts."""


from fastapi import APIRouter, Depends
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
)

from ..graph import queries
from . import deps
from .deps import UI_DIST, get_conn

router = APIRouter()


@router.get("/", include_in_schema=False)
def root():
    """The web UI once built (cd ui && npm run build), otherwise the API docs."""
    index = UI_DIST / "index.html"
    return FileResponse(index) if index.exists() else RedirectResponse("/docs")


@router.get("/health")
def health():
    return {"ok": True, "pack": deps.PACK.name}


@router.get("/stats")
def stats(conn=Depends(get_conn)):
    return {**queries.stats(conn, deps.PACK), "rebuild": deps.rebuilder.status()}

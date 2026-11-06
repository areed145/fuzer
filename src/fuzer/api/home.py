"""The page itself, health and counts."""


from fastapi import APIRouter, Depends
from fastapi.responses import (
    FileResponse,
    JSONResponse,
)

from ..graph import queries
from . import deps
from .deps import UI_DIST, get_conn

from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/", include_in_schema=False)
def root():
    """The web UI once built (cd ui && npm run build), otherwise the API docs."""
    index = UI_DIST / "index.html"
    return FileResponse(index) if index.exists() else JSONResponse(
        {"detail": "The web UI is not built. The deployment owner must build ui before serving Fuzer."}, status_code=503)


@router.get("/health")
def health():
    return {"ok": True, "pack": deps.PACK.name}


@router.get("/stats")
def stats(conn=Depends(get_conn)):
    return {**queries.stats(conn, deps.PACK), "rebuild": deps.rebuilder.status()}

"""The page itself, health and counts."""


from fastapi import APIRouter, Depends
from fastapi.responses import (
    FileResponse,
    JSONResponse,
)

from ..graph import operations, queries
from . import deps
from .deps import UI_DIST, get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)  # HEAD: Render's port probe
def root():
    """The web UI once built (cd ui && npm run build), otherwise the API docs."""
    index = UI_DIST / "index.html"
    return FileResponse(index) if index.exists() else JSONResponse(
        {"detail": "The web UI is not built. The deployment owner must build ui before serving Fuzer."}, status_code=503)


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    """The installed app's offline shell (P5.14d): part of the shell, so public like "/". Served from the root so it can
    look after the whole app, and never cached, so a new build's worker is picked up on the next visit."""
    worker = UI_DIST / "sw.js"
    if not worker.exists():
        return JSONResponse({"detail": "The web UI is not built."}, status_code=404)
    return FileResponse(worker, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/health")
def health():
    try:
        pack = deps.pack().name
    except Exception:
        pack = None
    return {"ok": True, "pack": pack}


@router.get("/stats")
def stats(conn=Depends(get_conn)):
    return {**queries.stats(conn, deps.pack()), "data_through": operations.data_through(conn),
            "rebuild": deps.rebuilder.status()}

"""The page itself, health and counts."""

import base64
import functools
import re

from fastapi import APIRouter, Depends
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)

from ..graph import operations, queries
from . import deps
from .deps import UI_DIST, get_conn
from .security import ROOT_ICONS, AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


# The page's icons go in the page itself. iOS's Add to Home Screen shows a linked icon in its sheet, then fetches it
# again when it saves the app, and that fetch doesn't make it: the Home Screen got a letter tile. An inlined one is
# saved as it is.
INLINED = re.compile(r'href="/assets/((?:apple-touch-icon|favicon-32)\.png)"')


@functools.cache
def _page(stamp: float) -> str:
    def inline(m: re.Match) -> str:
        icon = UI_DIST / "assets" / m[1]
        return f'href="data:image/png;base64,{base64.b64encode(icon.read_bytes()).decode()}"' if icon.is_file() else m[0]
    return INLINED.sub(inline, (UI_DIST / "index.html").read_text(encoding="utf-8"))


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)  # HEAD: Render's port probe
def root():
    """The web UI once built (cd ui && npm run build), otherwise the API docs."""
    index = UI_DIST / "index.html"
    return HTMLResponse(_page(index.stat().st_mtime)) if index.exists() else JSONResponse(
        {"detail": "The web UI is not built. The deployment owner must build ui before serving Fuzer."}, status_code=503)


def _icon(name: str):
    icon = UI_DIST / "assets" / name
    if not icon.is_file():
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(icon, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


# The icons iPhones and browsers ask for at the root on their own, whatever the page links. Public like "/", and
# answering HEAD too: an icon fetcher may ask with HEAD first, and a 405 there reads as no icon.
def _touch_icon():
    return _icon("apple-touch-icon.png")


def _favicon():
    return _icon("favicon-32.png")


for _path in ROOT_ICONS:
    router.add_api_route(_path, _favicon if _path == "/favicon.ico" else _touch_icon, methods=["GET", "HEAD"],
                         include_in_schema=False)


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

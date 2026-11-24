"""Reports (docs/reports.md): report pages drawn from definitions, each panel read from the application's own model.

    GET /reports                                     every report: key, title, who reads it, scope kinds, window
    GET /reports/{key}?scope&window&at               the definition, the scope and window it resolves to, the scopes
                                                     it can take and the entities each repeat row draws for
    GET /reports/{key}/panels/{id}?scope&window&at   one panel's result, so a slow panel costs only itself
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ..core import reports
from ..core.jsonsafe import plain
from ..core.windows import WindowError
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


def _refused(e: Exception) -> HTTPException:
    return HTTPException(404 if str(e).startswith("no report") else 400, str(e))


@router.get("/reports")
def listing():
    """Every report a person can open, the platform's and the bundle's."""
    return [{"key": d["key"], "title": d["title"], "for": d["for"], "scope": d["scope"],
             "window": {"default": d["window"]["default"]}, "source": d["source"]}
            for d in reports.load(deps.pack()).values()]


@router.get("/reports/{key}")
def report(key: str, scope: str | None = None, window: str | None = None, at: date | None = None,
           conn=Depends(get_conn)):
    """A report for a scope and window."""
    from ..ops import report_pages

    try:
        return plain(report_pages.view(conn, deps.pack(), key, scope, window, at))
    except (reports.ReportError, WindowError) as e:
        raise _refused(e) from e


@router.get("/reports/{key}/panels/{panel_id}")
def panel(key: str, panel_id: str, scope: str | None = None, window: str | None = None, at: date | None = None,
          conn=Depends(get_conn)):
    """One panel of a report for a scope and window: the same numbers the metric, the trend and the Work list show."""
    from ..core.shared_reads import shared
    from ..ops import report_pages

    try:
        return shared(conn, "report-panel", (key, panel_id, scope, window, at),
                      lambda: plain(report_pages.panel_data(conn, deps.pack(), key, panel_id, scope, window, at)))
    except (reports.ReportError, WindowError) as e:
        raise _refused(e) from e

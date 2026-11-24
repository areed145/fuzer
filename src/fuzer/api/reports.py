"""Reports (docs/reports.md): report pages drawn from definitions, each panel read from the application's own model.

    GET /reports                                     every report: key, title, who reads it, scope kinds, window
    GET /reports/{key}?scope&window&at               the definition, the scope and window it resolves to, the scopes
                                                     it can take and the entities each repeat row draws for
    GET /reports/{key}/panels/{id}?scope&window&at   one panel's result, so a slow panel costs only itself
    GET /model?kinds                                 the measures, grains and row sets an editor can pick

Custom reports (ops/custom_reports.py), each `custom-<id>`, open to their owner and to whoever they are shared with:

    POST /reports/{key}/copy                         a private copy of any report, keeping its origin
    POST /reports/custom                             a blank one: {title, kinds?}
    PUT /reports/{key}                               save the next version: {definition or text, base}
    POST /reports/check                              check a definition without saving: {definition or text}
    GET /reports/{key}/definition                    the definition as YAML text
    POST /reports/{key}/draft/panels/{id}            one panel of a definition being edited, before it is saved
    POST /reports/{key}/share                        {shared: '' | everyone | role:<name>}; beyond yourself needs configure
    GET /reports/{key}/versions                      every kept version
    POST /reports/{key}/versions/{version}/restore   bring one back as the newest
    DELETE /reports/{key}                            take it down (kept, marked removed)
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import reports
from ..core.jsonsafe import plain
from ..core.windows import WindowError
from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


def _refused(e: Exception) -> HTTPException:
    return HTTPException(404 if str(e).startswith("no report") else 400, str(e))


def _who(conn, by: str) -> tuple[str, list[str], bool]:
    """Who is asking: their actor, the workflow roles they hold, and whether they may share beyond themselves
    (configure access). A loopback development run (no signed-in account) names itself with `by`."""
    from ..workflow.definitions import roles_of
    from .security import current

    person = current.get()
    actor = person.actor if person is not None else (by or "anonymous")
    return actor, roles_of(conn, actor), person is None or person.allows("configure")


def _open(conn, key: str, by: str):
    """A custom report the person may open (404 as if it weren't there otherwise); None for any other report."""
    from ..ops import custom_reports as cr

    if not cr.is_custom(key):
        return None
    try:
        row = cr.get(conn, key)
    except reports.ReportError as e:
        raise _refused(e) from e
    actor, roles, _ = _who(conn, by)
    if not cr.readable(row, actor, roles):
        raise HTTPException(404, f"no report {key}")
    return row


@router.get("/reports")
def listing(by: str = "anonymous", conn=Depends(get_conn)):
    """Every report a person can open: the platform's and the bundle's, then their own and those shared with them."""
    from ..ops import custom_reports as cr

    actor, roles, _ = _who(conn, by)
    return plain([{"key": d["key"], "title": d["title"], "for": d["for"], "scope": d["scope"],
                   "window": {"default": d["window"]["default"]}, "source": d["source"]}
                  for d in reports.load(deps.pack()).values()] + cr.listing(conn, actor, roles, deps.pack()))


@router.get("/reports/{key}")
def report(key: str, scope: str | None = None, window: str | None = None, at: date | None = None,
           by: str = "anonymous", conn=Depends(get_conn)):
    """A report for a scope and window. A custom report also says who owns it, who it is shared with, where it was
    copied from and whether the person may change or share it."""
    from ..ops import custom_reports as cr
    from ..ops import report_pages

    row = _open(conn, key, by)
    try:
        out = report_pages.view(conn, deps.pack(), key, scope, window, at)
    except (reports.ReportError, WindowError) as e:
        raise _refused(e) from e
    actor, _, may_share = _who(conn, by)
    out["custom"] = None if row is None else {**cr.about(conn, key, actor, deps.pack()), "can_share": may_share}
    return plain(out)


@router.get("/reports/{key}/panels/{panel_id}")
def panel(key: str, panel_id: str, scope: str | None = None, window: str | None = None, at: date | None = None,
          by: str = "anonymous", conn=Depends(get_conn)):
    """One panel of a report for a scope and window: the same numbers the metric, the trend and the Work list show."""
    from ..core.shared_reads import shared
    from ..ops import report_pages

    _open(conn, key, by)
    try:
        return shared(conn, "report-panel", (key, panel_id, scope, window, at),
                      lambda: plain(report_pages.panel_data(conn, deps.pack(), key, panel_id, scope, window, at)))
    except (reports.ReportError, WindowError) as e:
        raise _refused(e) from e


# --- the model an editor picks from ------------------------------------------------------------------------------------

@router.get("/model")
def model(kinds: str | None = None):
    """The measures (with their grains), the row sets and the panel types an editor can pick. With `kinds` (the
    report's scope kinds, comma separated), each measure says whether it answers for those scopes or only for the
    whole register, and grains at or above the scope (cutting an area's report by facility) are left out."""
    from ..core import metrics, rowsets
    from ..graph.hierarchy import KIND_RANK

    pack = deps.pack()
    asked = [k for k in (kinds or "").split(",") if k]
    top = min((KIND_RANK.get(k, 99) for k in asked), default=-1)

    def grains(gs):
        return [g for g in gs if g not in KIND_RANK or KIND_RANK[g] > top]

    measures = [{"key": m.key, "label": m.label, "group": m.group, "unit": m.unit, "definition": m.definition,
                 "grains": grains(m.grains), "scoped": m.scoped, "windowed": m.windowed}
                for m in metrics.registry(pack).values()]
    sets = [{"key": r.key, "label": r.label, "definition": r.definition, "scoped": r.scoped}
            for r in rowsets.registry(pack).values()]
    return {"kinds": asked, "types": list(reports.TYPES), "charts": list(reports.CHARTS),
            "case_filters": list(reports.CASE_FILTERS), "measures": measures, "row_sets": sets}


# --- custom reports ----------------------------------------------------------------------------------------------------

class CopyIn(BaseModel):
    title: str | None = None
    by: str = "anonymous"


class BlankIn(BaseModel):
    title: str
    kinds: list[str] | None = None
    by: str = "anonymous"


class DefinitionIn(BaseModel):
    definition: dict | None = None
    text: str | None = None  # YAML or JSON, from the definition view
    base: int | None = None  # the version it was edited from
    by: str = "anonymous"


class DraftPanelIn(BaseModel):
    definition: dict
    scope: str | None = None
    window: str | None = None
    at: date | None = None
    by: str = "anonymous"


class ShareIn(BaseModel):
    shared: str = ""
    by: str = "anonymous"


def _definition(body) -> dict:
    from ..ops import custom_reports as cr

    if body.text is not None:
        return cr.parse(body.text)
    if body.definition is None:
        raise reports.ReportError("send the definition, or its text")
    return body.definition


def _changing(fn):
    """Run a change to a custom report, answering its refusals with why."""
    from ..ops import custom_reports as cr

    try:
        return fn()
    except cr.NotYours as e:
        raise HTTPException(403, str(e)) from None
    except cr.Stale as e:
        raise HTTPException(409, str(e)) from None
    except (reports.ReportError, WindowError) as e:
        raise _refused(e) from None


def _saved(conn, row: dict, by: str) -> dict:
    from ..ops import custom_reports as cr

    actor, _, may_share = _who(conn, by)
    return plain({**cr.about(conn, cr.key_of(row["id"]), actor, deps.pack()), "can_share": may_share})


@router.post("/reports/custom")
def custom_blank(body: BlankIn, conn=Depends(get_conn)):
    """Start a custom report with no panels yet, private to its maker."""
    from ..ops import custom_reports as cr

    actor, _, _ = _who(conn, body.by)
    return _saved(conn, _changing(lambda: cr.blank(conn, actor, body.title, body.kinds)), body.by)


@router.post("/reports/check")
def custom_check(body: DefinitionIn):
    """Check a definition as a save would, without keeping it: the definition with its defaults, or why not."""
    from ..ops import custom_reports as cr

    def check():
        d = _definition(body)
        return reports.check({**d, "key": f"{reports.CUSTOM_PREFIX}0", "version": 1}, "custom")
    d = _changing(check)
    return plain({"definition": d, "text": cr.dump(d)})


@router.post("/reports/{key}/copy")
def custom_copy(key: str, body: CopyIn, conn=Depends(get_conn)):
    """A private copy of any report the person can open, keeping the report and version it came from."""
    from ..ops import custom_reports as cr

    _open(conn, key, body.by)
    actor, _, _ = _who(conn, body.by)
    return _saved(conn, _changing(lambda: cr.copy(conn, deps.pack(), key, actor, body.title)), body.by)


@router.put("/reports/{key}")
def custom_save(key: str, body: DefinitionIn, conn=Depends(get_conn)):
    """Save a custom report's next version, checked as a standard report is. Only its owner saves it."""
    from ..ops import custom_reports as cr

    _open(conn, key, body.by)
    if not cr.is_custom(key):
        raise HTTPException(403, "standard reports are kept in code; copy it to your reports to change it")
    actor, _, _ = _who(conn, body.by)
    return _saved(conn, _changing(lambda: cr.save(conn, key, _definition(body), actor, body.base)), body.by)


@router.get("/reports/{key}/definition")
def custom_text(key: str, by: str = "anonymous", conn=Depends(get_conn)):
    """Any report's definition as YAML, as the definition view shows it."""
    from ..ops import custom_reports as cr
    from ..ops import report_pages

    _open(conn, key, by)
    try:
        d = report_pages.definition(conn, deps.pack(), key)
    except reports.ReportError as e:
        raise _refused(e) from e
    return {"key": key, "version": d["version"], "text": cr.dump(d)}


@router.post("/reports/{key}/draft/panels/{panel_id}")
def custom_draft_panel(key: str, panel_id: str, body: DraftPanelIn, conn=Depends(get_conn)):
    """One panel of a definition being edited (a panel added and not saved yet), read as the saved page reads it.
    Changes nothing."""
    from ..ops import report_pages

    _open(conn, key, body.by)

    def read():
        d = reports.check({**body.definition, "key": f"{reports.CUSTOM_PREFIX}0", "version": 1}, "custom")
        return report_pages.panel_data(conn, deps.pack(), key, panel_id, body.scope, body.window, body.at, draft=d)
    return plain(_changing(read))


@router.post("/reports/{key}/share")
def custom_share(key: str, body: ShareIn, conn=Depends(get_conn)):
    """Who else sees a custom report. Sharing with everyone or a role needs configure access; anyone may make their
    own report private again."""
    from ..ops import custom_reports as cr

    _open(conn, key, body.by)
    actor, _, may_share = _who(conn, body.by)
    if body.shared and not may_share:
        raise HTTPException(403, "Sharing a report with others needs configure access. Ask an owner to share it.")
    return _saved(conn, _changing(lambda: cr.share(conn, key, body.shared, actor)), body.by)


@router.get("/reports/{key}/versions")
def custom_versions(key: str, by: str = "anonymous", conn=Depends(get_conn)):
    """Every kept version of a custom report, newest first."""
    from ..ops import custom_reports as cr

    _open(conn, key, by)
    if not cr.is_custom(key):
        raise HTTPException(404, "standard reports keep their history in code")
    return plain(_changing(lambda: cr.versions(conn, key)))


@router.post("/reports/{key}/versions/{version}/restore")
def custom_restore(key: str, version: int, by: str = "anonymous", conn=Depends(get_conn)):
    """Bring back an earlier version as the newest; the versions in between stay kept."""
    from ..ops import custom_reports as cr

    _open(conn, key, by)
    actor, _, _ = _who(conn, by)
    return _saved(conn, _changing(lambda: cr.restore(conn, key, version, actor)), by)


@router.delete("/reports/{key}")
def custom_remove(key: str, by: str = "anonymous", conn=Depends(get_conn)):
    """Take down your own custom report (it is kept, marked removed)."""
    from ..ops import custom_reports as cr

    _open(conn, key, by)
    if not cr.is_custom(key):
        raise HTTPException(403, "standard reports are kept in code")
    actor, _, _ = _who(conn, by)
    _changing(lambda: cr.remove(conn, key, actor))
    return {"ok": True}

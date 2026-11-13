"""Conformance (P8.2) and change control (P8.3): for every asset the person may open, the operating model it runs, its deviations and whether
they are justified, and its work in practice. Only granted projects are read; the rest are named (D6)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .deps import get_conn
from .security import AuthenticatedRoute
from .workflow import WorkBy

router = APIRouter(route_class=AuthenticatedRoute)


@router.get("/standards/conformance")
def conformance(days: int = 28):
    """Each asset against its operating model (P8.2): version, deviations with their owner and review date, a verdict
    (standard, justified or drifted) and its work in practice over `days`. Reads only."""
    from ..core import access, db
    from ..workflow import conformance as standards
    from .security import current

    if days < 1 or days > 366:
        raise HTTPException(400, "a period is 1 to 366 days")
    allowed = None
    if (who := current.get()) is not None:
        with db.connect() as conn:
            allowed = access.projects_for(conn, who)
    return standards.fleet(allowed, days)


class ProposeIn(BaseModel):
    changes: dict = Field(default_factory=dict)
    summary: str
    by: str
    promote: dict | None = None  # {part, key, project?}: propose that project's own value there instead


class ReviewIn(BaseModel):
    approve: bool
    note: str
    by: str


class RolloutIn(BaseModel):
    project: str
    version: int
    by: str


class RollbackIn(BaseModel):
    reason: str
    by: str


def _changes(fn, *a, **kw):
    from ..workflow import model_changes as mc

    try:
        return fn(*a, **kw)
    except mc.ChangeError as e:
        raise HTTPException(400, str(e)) from None


def _granted(project: str) -> None:
    """A rollout or rollback reaches only an asset the person is granted (D6)."""
    from ..core import access, db
    from .security import current

    if (who := current.get()) is None:
        return
    with db.connect() as conn:
        allowed = access.projects_for(conn, who)
    if allowed is not None and project not in allowed:
        raise HTTPException(403, f"you aren't granted {project}")


@router.get("/standards/versions")
def versions(conn=Depends(get_conn)):
    """Change control (P8.3): every version of the standard with its state, test run and review, the latest
    published, and each asset's rollouts and rollbacks."""
    from ..workflow import model_changes as mc

    return {"versions": mc.versions(conn), "latest": mc.latest(conn), "rollouts": mc.history(conn)}


@router.post("/standards/versions")
def propose(b: ProposeIn, conn=Depends(get_conn)):
    """Propose a new version of the standard: `changes` ({part: {key: value or null}}), or `promote` a project's own
    value at a part and key (this project's, or a granted one named as `project`)."""
    from ..core import db, projects
    from ..workflow import model_changes as mc

    if b.promote:
        part, key, project = b.promote.get("part"), b.promote.get("key"), b.promote.get("project")
        if not project or project == projects.current(conn):
            return _changes(mc.promote, conn, part, key, b.summary, b.by)
        _granted(project)
        if project not in projects.stored(conn):
            raise HTTPException(404, f"no project {project!r}")
        with projects.use(conn, project), db.connect() as there:
            return _changes(mc.promote, there, part, key, b.summary, b.by)
    return _changes(mc.propose, conn, b.changes, b.summary, b.by)


@router.post("/standards/versions/{version}/test-run")
def test_run(version: int, conn=Depends(get_conn)):
    """Run a proposal against this project's closed cases of the work types it touches."""
    from ..workflow import model_changes as mc

    return _changes(mc.test_run, conn, "standard", version)


@router.post("/standards/versions/{version}/review")
def review(version: int, b: ReviewIn, conn=Depends(get_conn)):
    """Approve or reject a tested proposal (workflow_admin, not its author), with a note."""
    from ..workflow import model_changes as mc

    return _changes(mc.review, conn, "standard", version, b.by, b.approve, b.note)


@router.post("/standards/versions/{version}/publish")
def publish(version: int, b: WorkBy, conn=Depends(get_conn)):
    """Publish an approved version so assets can run it (workflow_admin)."""
    from ..workflow import model_changes as mc

    return _changes(mc.publish, conn, "standard", version, b.by)


@router.post("/standards/rollouts")
def rollout(b: RolloutIn, conn=Depends(get_conn)):
    """Roll a published version out to one asset, keeping what it ran before for a rollback."""
    from ..workflow import model_changes as mc

    _granted(b.project)
    return _changes(mc.rollout, conn, b.project, "standard", b.version, b.by)


@router.post("/standards/rollouts/{project}/rollback")
def rollback(project: str, b: RollbackIn, conn=Depends(get_conn)):
    """Take one asset back to the version it ran before its last rollout."""
    from ..workflow import model_changes as mc

    _granted(project)
    return _changes(mc.rollback, conn, project, b.by, b.reason)

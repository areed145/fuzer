"""Sources (#132): the tables in the data folder with their connector, mapping and state, and a preview of
one before it loads, and adding a new one in one flow: suggest a mapping (rules, or Claude), save it and load
the table."""


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import deps
from .deps import get_conn
from .security import AuthenticatedRoute

router = APIRouter(route_class=AuthenticatedRoute)


def _data_dir() -> str:
    """The exports folder of the project this request runs for."""
    from ..core import projects

    return projects.field("data") or "data"


@router.get("/sources")
def sources_list(conn=Depends(get_conn)):
    from ..sources import preview

    return {"data_dir": _data_dir(), "sources": preview.listing(conn, _data_dir())}


@router.get("/sources/{name}")
def source_preview(name: str, rows: int = 5, conn=Depends(get_conn)):
    from ..sources import preview

    try:
        return preview.preview(conn, deps.pack(), _data_dir(), name, min(max(rows, 1), 50))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class Suggest(BaseModel):
    claude: bool = False  # ask Claude for the columns rules can't name (costs money; cached per table)


@router.post("/sources/{name}/suggest")
def source_suggest(name: str, body: Suggest, conn=Depends(get_conn)):
    """A draft mapping for a table with none, to edit before saving. Nothing is stored but Claude's cache."""
    from pathlib import Path

    from ..sources import connectors, suggest

    c = connectors.find(Path(_data_dir()), name)
    if c is None:
        raise HTTPException(404, f"no table {name!r} in {_data_dir()}")
    s = suggest.suggest_table(conn, deps.pack(), name, c, claude=body.claude)
    return {"yaml": suggest.to_yaml(s["mapping"]), "key": s["suggestion"]["key"],
            "columns": {k: {"meaning": v["meaning"], "by": v["by"]} for k, v in s["suggestion"]["columns"].items()},
            "claude": s["claude"] or None}


class Load(BaseModel):
    yaml: str | None = None  # a mapping to save first (a new table, or a changed mapping)
    by: str = "anonymous"


@router.post("/sources/{name}/load")
def source_load(name: str, body: Load, conn=Depends(get_conn)):
    """Save the mapping (when given) and load this one table now. Its records become entities on the next
    rebuild. A table whose columns changed stays held until its schema review is accepted."""
    from ..sources import connections, ingest, suggest

    if body.yaml is not None:
        try:
            suggest.save(conn, name, body.yaml, body.by)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    try:
        return ingest.ingest(conn, _data_dir(), exports=[name], trigger="api")
    except (ingest.DatasetNotFound, ingest.DatasetUnavailable) as e:
        conn.rollback()
        raise HTTPException(404, str(e)) from e
    except connections.ConnectionError_ as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e

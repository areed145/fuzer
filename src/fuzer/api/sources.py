"""Sources (#132): the tables in the data folder with their connector, mapping and state, and a preview of
one before it loads. Reads only."""

import os

from fastapi import APIRouter, Depends, HTTPException

from . import deps
from .deps import get_conn

router = APIRouter()


def _data_dir() -> str:
    return os.environ.get("FUZER_DATA_DIR") or "data"


@router.get("/sources")
def sources_list(conn=Depends(get_conn)):
    from ..sources import preview

    return {"data_dir": _data_dir(), "sources": preview.listing(conn, _data_dir())}


@router.get("/sources/{name}")
def source_preview(name: str, rows: int = 5, conn=Depends(get_conn)):
    from ..sources import preview

    try:
        return preview.preview(conn, deps.PACK, _data_dir(), name, min(max(rows, 1), 50))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e

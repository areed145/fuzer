"""REST API: search, entity detail, lookup, flow tracing, the review queue and the work engine."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..core import db
from ..workflow import engine as work
from . import (
    architecture,
    assistant,
    catalog,
    entities,
    graph,
    home,
    jobs,
    knowledge,
    ontology,
    ops,
    settings,
    sources,
    workflow,
)
from .deps import PACK, UI_DIST, get_conn, rebuilder  # noqa: F401  (fuzer.api.PACK and the rest, as before)


@asynccontextmanager
async def lifespan(app):
    # Bring an older database up to date (idempotent), as every CLI command does; otherwise pages
    # that read newer tables fail on a database made by an earlier version.
    with db.connect() as conn:
        db.init_schema(conn)
        work.load_defaults(conn)
    yield


app = FastAPI(title="fuzer", version="0.1.0", lifespan=lifespan)

if (UI_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

for module in (home, entities, knowledge, ops, assistant, ontology, graph, workflow, settings, catalog, sources, architecture, jobs):  # in this order: FastAPI matches routes in the order they were added
    app.include_router(module.router)
app.add_exception_handler(work.WorkError, workflow.work_error)

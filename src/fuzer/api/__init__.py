"""REST API: search, entity detail, lookup, flow tracing, the review queue and the work engine."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from psycopg.errors import QueryCanceled

from ..core import access, db
from ..core.gates import GateRefused
from ..sources import catalog as source_catalog
from ..workflow import engine as work
from . import (
    assistant,
    auth,
    catalog,
    entities,
    graph,
    home,
    hooks,
    investigation,
    jobs,
    knowledge,
    metrics,
    ontology,
    ops,
    settings,
    sources,
    stewardship,
    workflow,
)
from .deps import (  # noqa: F401  (fuzer.api.rebuilder and the rest, as before)
    UI_DIST,
    get_conn,
    rebuilder,
)
from .security import ProjectScope, RequestBounds, query_timeout, server_error


@asynccontextmanager
async def lifespan(app):
    # Bring an older database up to date (idempotent), as every CLI command does; otherwise pages
    # that read newer tables fail on a database made by an earlier version.
    access.mode()
    if access.configured():
        access.public_url()
    with db.connect() as conn:
        db.init_schema(conn)
        work.load_defaults(conn)
        from ..ops import routing
        moved = routing.adopt_open_cases(conn)  # cases opened plain before #509 run as their process now
        if moved["moved"] or moved["failed"]:
            logging.getLogger(__name__).info("open cases moved onto processes: %s", moved)
        access.seed(conn)
        source_catalog.ensure(conn)
        conn.commit()
    yield


app = FastAPI(title="fuzer", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ProjectScope)
app.add_middleware(RequestBounds)
app.include_router(auth.router)
app.include_router(hooks.router)  # authenticated by each system's shared secret, not a session

if (UI_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

for module in (home, investigation, entities, knowledge, ops, metrics, assistant, ontology, graph, workflow, settings, catalog, sources, stewardship, jobs):  # in this order: FastAPI matches routes in the order they were added
    app.include_router(module.router)
app.add_exception_handler(work.WorkError, workflow.work_error)
app.add_exception_handler(GateRefused, workflow.work_error)
app.add_exception_handler(QueryCanceled, query_timeout)
app.add_exception_handler(Exception, server_error)  # a 500: reported to GitHub issues when configured

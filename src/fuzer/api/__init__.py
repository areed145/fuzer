"""REST API: search, entity detail, lookup, flow tracing, the review queue and the work engine."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from psycopg.errors import QueryCanceled

from ..core import access, db
from ..sources import catalog as source_catalog
from ..workflow import engine as work
from . import (
    assistant,
    auth,
    catalog,
    entities,
    graph,
    home,
    investigation,
    jobs,
    knowledge,
    ontology,
    ops,
    settings,
    sources,
    workflow,
)
from .deps import UI_DIST, get_conn, rebuilder  # noqa: F401  (fuzer.api.rebuilder and the rest, as before)
from .security import ProjectScope, RequestBounds, query_timeout


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
        access.seed(conn)
        source_catalog.ensure(conn)
        conn.commit()
    yield


app = FastAPI(title="fuzer", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ProjectScope)
app.add_middleware(RequestBounds)
app.include_router(auth.router)

if (UI_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

for module in (home, investigation, entities, knowledge, ops, assistant, ontology, graph, workflow, settings, catalog, sources, jobs):  # in this order: FastAPI matches routes in the order they were added
    app.include_router(module.router)
app.add_exception_handler(work.WorkError, workflow.work_error)
app.add_exception_handler(QueryCanceled, query_timeout)

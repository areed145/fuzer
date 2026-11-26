"""One HTTP access boundary, including routes that read settings or trigger work on GET."""

import asyncio
import functools
import hmac
import inspect
import json
import re
import time
from contextvars import ContextVar

import psycopg
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from ..core import access, db, settings

current: ContextVar[access.Principal | None] = ContextVar("fuzer_http_principal", default=None)
mutating: ContextVar[bool] = ContextVar("fuzer_http_mutating", default=False)
reading: ContextVar[bool] = ContextVar("fuzer_http_reading", default=False)  # a GET or HEAD, signed in or not
# The shell is public: the page, its offline worker (P5.14d) and health. Everything else needs a session.
# The icons iPhones and browsers ask for at the root on their own: public like the page they belong to.
ROOT_ICONS = ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
              "/apple-touch-icon-180x180.png", "/apple-touch-icon-180x180-precomposed.png",
              "/apple-touch-icon-120x120.png", "/apple-touch-icon-120x120-precomposed.png")
PUBLIC_SHELL = ("/", "/sw.js", "/health", *ROOT_ICONS)
HASHED_ASSET = re.compile(r"/assets/[^/]+-[A-Za-z0-9_-]{8}\.[a-z0-9]+$")  # Vite's name-<content hash>.ext
MAX_BODY = 32 * 1024 * 1024  # Four existing 5 MB assistant images, base64 encoded, plus JSON.
_active = 0
ADMISSION_WAIT_SECONDS = 10.0  # how long a request over access.api_concurrency waits for a place
ADMISSION_POLL_SECONDS = 0.05


async def json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        raise HTTPException(422, "Expected valid JSON") from e
    if not isinstance(body, dict):
        raise HTTPException(422, "Expected a JSON object")
    return body


def query_timeout(request, error):
    return JSONResponse({"detail": "This query exceeded the server time limit. Try a narrower scope or date range."},
                        status_code=504)


def server_error(request, error):
    """A request that ended in an unhandled exception: reported to GitHub issues (when configured), then the 500.
    Starlette still logs the traceback and re-raises it after this response."""
    from ..core import bugreports

    bugreports.reporter.report(error, f"{request.method} {request.url.path}")
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


def cookie_name(kind: str) -> str:
    return ("__Host-" if access.public_url().startswith("https://") else "") + "fuzer_" + kind


def local_request(request: Request) -> bool:
    return (request.client is not None and request.client.host in ("127.0.0.1", "::1", "testclient")
            and request.url.hostname in ("localhost", "127.0.0.1", "::1", "testserver"))


def identify(request: Request) -> access.Principal | None:
    if access.mode() == "local":
        if not local_request(request):
            raise HTTPException(403, "Local attribution mode accepts loopback requests only; configure Microsoft sign-in")
        return None
    if not access.configured():
        raise HTTPException(503, "Microsoft sign-in is not configured. Contact the deployment owner.")
    token = request.cookies.get(cookie_name("session"), "")
    with db.connect() as conn:
        person = access.session(conn, token) if token else None
    if person is None:
        raise HTTPException(401, "Sign in with Microsoft to continue")
    return person


def csrf_check(request: Request, person: access.Principal):
    origin = request.headers.get("origin")
    if origin and origin != access.public_url():
        raise HTTPException(403, "This action must come from the Fuzer application")
    if not hmac.compare_digest(request.headers.get("x-fuzer-csrf", "").encode(), person.csrf.encode()):
        raise HTTPException(403, "Session confirmation is missing or expired; reload the page and try again")


def require(person: access.Principal, permission: str):
    if not person.allows(permission):
        raise HTTPException(403, f"This action requires {permission} access. Ask an owner to update your roles.")


def permission(path: str, method: str) -> str:
    if path == "/demo/config" and method == "GET":
        return "read"
    if path == "/ontology/declared" and method == "GET":
        return "read"
    if path == "/assistant/ask" or path.endswith("/remember"):
        return "ai"
    if path.startswith(("/settings", "/sources", "/ontology", "/jobs", "/rebuild")):
        return "configure"
    if method not in ("GET", "HEAD"):
        if (path.startswith(("/work/definitions", "/work/forms", "/work/roles", "/work/people", "/exceptions/types"))
                or path == "/work/tick" or path.endswith("/migrate")):
            return "configure"
        return "operate"
    return "read"


# What a data steward does to keep loads flowing (fix a held load, rerun, profile, accept a check or a mapping
# version, rematch, rebuild): routes under /sources and /rebuild that the demo account may use while every other
# configuration change (systems, connections, settings, ontology, roles, accounts) stays read only for it.
STEWARD_ACTIONS = frozenset({
    "/sources/datasets/{name}/run", "/sources/datasets/{name}/profile", "/sources/datasets/{name}/checks",
    "/sources/datasets/{name}/pause", "/sources/datasets/{name}/resume", "/sources/{name}/load",
    "/sources/connections/{cid}/test", "/sources/mappings/{version_id}/approve",
    "/sources/mappings/{version_id}/discard", "/sources/mappings/{version_id}/rollback", "/sources/rematch",
    "/rebuild"})


# Routes that call a model for whoever asks, beyond /generate and /remember: they need AI access as well as their own.
# Publishing a skill draft asks its eval questions of the live version and of the draft (agents.skill_gate).
# Running a skill's eval asks its questions of the agents, and grading a run asks the grader (agents.eval_runs).
PAID_PATHS = frozenset({"/assistant/ask", "/work/skills/publish", "/evals/skills/run", "/evals/runs/{run_id}/grade"})


def steward_action(path: str, method: str) -> bool:
    """`path` is the route's template, as `self.path` holds it."""
    return method == "POST" and path in STEWARD_ACTIONS


def _arguments(kwargs):
    """Bind audit actors after validation; do not rewrite nested workflow data or read sort parameters."""
    person = current.get()
    if person is None or not mutating.get():
        return
    for value in kwargs.values():
        if isinstance(value, BaseModel) and "by" in type(value).model_fields:
            value.by = person.actor
    if "by" in kwargs:
        kwargs["by"] = person.actor


def _committed(values, result):
    """Commit the route's connections before its answer is sent, then hand the answer back (P5.28d, #1392). The
    connection's own close (`get_conn`) runs only after the answer has gone, so the browser had a write's answer while
    its transaction was still open: the reads it made at once, and on the 1-CPU server those made seconds later, still
    saw the case with its old owner and the old counts until the page was loaded again. A failed route commits nothing:
    its exception skips this and the close rolls back."""
    for value in values.values():
        if isinstance(value, psycopg.Connection) and not value.closed \
                and value.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            value.commit()
    return result


def _read_again(values) -> bool:
    """Whether a read the database chose as a deadlock's victim may run once more: True after rolling back the
    connections it was given. On a zero-downtime deploy the new instance's schema update locks tables while the old
    one still answers reads, and a read holding one table while waiting on another can be the side Postgres cancels
    (#1332). A read changes nothing, so running it again once the update has its locks is safe; a write is never
    replayed (AGENTS.md)."""
    if not reading.get() or mutating.get():
        return False
    for value in values.values():
        if isinstance(value, psycopg.Connection):
            value.rollback()
    return True


class AuthenticatedRoute(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        # include_router reconstructs routes, so wrap each endpoint only once.
        if not getattr(endpoint, "_trusted_actor", False):
            original = endpoint
            if inspect.iscoroutinefunction(original):
                @functools.wraps(original)
                async def endpoint(**values):
                    _arguments(values)
                    try:
                        return _committed(values, await original(**values))
                    except psycopg.errors.DeadlockDetected:
                        if not _read_again(values):
                            raise
                    return _committed(values, await original(**values))
            else:
                @functools.wraps(original)
                def endpoint(**values):
                    _arguments(values)
                    try:
                        return _committed(values, original(**values))
                    except psycopg.errors.DeadlockDetected:
                        if not _read_again(values):
                            raise
                    return _committed(values, original(**values))
            endpoint._trusted_actor = True
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request):
            token = reading.set(request.method in ("GET", "HEAD"))
            try:
                return await guarded(request)
            finally:
                reading.reset(token)

        async def guarded(request: Request):
            global _active
            if self.path in PUBLIC_SHELL:
                return await handler(request)
            began = time.perf_counter()
            person = await run_in_threadpool(identify, request)
            if person is None:
                return await handler(request)
            if db.scope().project and requested_project(request):
                await run_in_threadpool(self._project_access, person)
            acting = access.ACTING.set(person.acting if person.inspect else None)  # the demo session's own roles
            unsafe = request.method not in ("GET", "HEAD")
            needed = permission(self.path, request.method)
            if unsafe and needed == "configure" and person.inspect and steward_action(self.path, request.method):
                needed = "operate"  # a data steward's day-to-day fixes, not configuration (see steward_action)
            if unsafe and needed == "configure" and person.inspect and not person.allows(needed):
                raise HTTPException(403, "The demo account can look at configuration but can't change it.")
            if unsafe or not person.reads(needed):
                require(person, needed)
            if unsafe:
                csrf_check(request, person)
            # Some existing GET previews can call a provider. Treat those explicitly as paid execution.
            try:
                claude = TypeAdapter(bool).validate_python(request.query_params.get("claude", "false"))
            except ValidationError as e:
                raise HTTPException(422, "claude must be a boolean") from e
            if (self.path in PAID_PATHS or self.path.endswith(("/generate", "/remember"))
                    or claude):
                require(person, "ai")
                csrf_check(request, person)
            if self.path.endswith("/suggest") and unsafe:
                body = await json_object(request)
                if body.get("claude"):
                    require(person, "ai")
            if self.path in ("/assistant/ask", "/work/tasks/bulk-complete"):
                body = await json_object(request)
                conversation_id = body.get("conversation_id")
                if conversation_id is not None and not isinstance(conversation_id, str):
                    raise HTTPException(422, "Expected a conversation identifier")
                task_ids = body.get("task_ids", [])
                if not isinstance(task_ids, list) or len(task_ids) > 1000 or any(type(t) is not int for t in task_ids):
                    raise HTTPException(422, "Expected at most 1,000 task identifiers")
                request.state.conversation_id = conversation_id
                request.state.task_ids = task_ids
            await run_in_threadpool(self._ownership, request, person)
            hidden = await run_in_threadpool(self._hidden, request, person)
            limit = await run_in_threadpool(settings.get, None, "access.api_concurrency")
            # A page opens with a handful of reads: one over the limit waits its turn for a few seconds rather
            # than fail the page, and is refused only when the server stays full.
            checked = time.perf_counter()
            waited = 0.0
            while _active >= limit:
                if waited >= ADMISSION_WAIT_SECONDS:
                    raise HTTPException(429, "The server is busy. Wait briefly and try again.",
                                        headers={"Retry-After": "2"})
                await asyncio.sleep(ADMISSION_POLL_SECONDS)
                waited += ADMISSION_POLL_SECONDS
            _active += 1
            token = current.set(person)
            mutation = mutating.set(unsafe)
            try:
                admitted = time.perf_counter()
                response = await handler(request)
                response = _scrubbed(response, hidden) if hidden else response
                # Where a read's time went, for the browser's network panel and for timing the live site: checking
                # who asks, waiting for a place, and the read itself (`app` includes waiting for the interpreter).
                response.headers["Server-Timing"] = (
                    f"auth;dur={(checked - began) * 1000:.0f}, queue;dur={(admitted - checked) * 1000:.0f}, "
                    f"app;dur={(time.perf_counter() - admitted) * 1000:.0f}")
                return response
            finally:
                current.reset(token)
                mutating.reset(mutation)
                access.ACTING.reset(acting)
                _active -= 1
        return protected

    def _ownership(self, request: Request, person: access.Principal):
        params = request.path_params
        with db.connect() as conn:
            conversation_id = params.get("conversation_id")
            if self.path == "/assistant/ask":
                # Body checks run on the event loop in protected(), before entering this method.
                conversation_id = getattr(request.state, "conversation_id", None)
            if conversation_id:
                row = conn.execute("select by_user from assistant_conversation where id = %s",
                                   (conversation_id,)).fetchone()
                if row is None or row["by_user"] != person.actor:
                    raise HTTPException(404, "Conversation not found in your account")
            if params.get("job_id"):
                row = conn.execute("select by_user from assistant_answer where id = %s", (params["job_id"],)).fetchone()
                if row is None:
                    from ..agents import service
                    row = service.owner(params["job_id"])
                if row is None or row["by_user"] != person.actor:
                    raise HTTPException(404, "Question not found in your account")
            if params.get("answer_id"):
                row = conn.execute("select by_user from assistant_answer where id = %s",
                                   (params["answer_id"],)).fetchone()
                if row is None or row["by_user"] != person.actor:
                    raise HTTPException(404, "Answer not found in your account")
            if params.get("draft_id"):
                row = conn.execute("select by_user from work_draft where id = %s", (params["draft_id"],)).fetchone()
                if row is None or row["by_user"] != person.actor:
                    raise HTTPException(404, "Draft not found in your account")
            if request.method not in ("GET", "HEAD") and params.get("task_id"):
                self._task(conn, person, params["task_id"])
            if request.method not in ("GET", "HEAD") and self.path.startswith("/work/items/") and params.get("item_id"):
                self._item(conn, person, params["item_id"])
            if request.method not in ("GET", "HEAD") and params.get("process_id"):
                try:
                    process_id = int(params["process_id"])
                except ValueError as e:
                    raise HTTPException(422, "Expected a numeric process identifier") from e
                row = conn.execute("select item_id from work_process where id = %s", (process_id,)).fetchone()
                if row:
                    self._item(conn, person, row["item_id"])
            if request.method not in ("GET", "HEAD") and params.get("exception_id"):
                self._exception(conn, person, params["exception_id"])
            if self.path == "/work/tasks/bulk-complete":
                for task_id in getattr(request.state, "task_ids", []):
                    self._task(conn, person, task_id)

    @staticmethod
    def _hidden(request: Request, person: access.Principal) -> frozenset[str]:
        """The restricted documents this person may not see here (R.5, #29); a route naming one answers 404."""
        with db.connect() as conn:
            hidden = access.hidden_documents(conn, person)
        if hidden and any(isinstance(v, str) and v in hidden for v in request.path_params.values()):
            raise HTTPException(404, "Not found")
        return hidden

    @staticmethod
    def _project_access(person: access.Principal):
        with db.connect() as conn:
            if db.scope().project not in access.projects_for(conn, person):
                raise HTTPException(403, "You don't have access to this project. Ask an owner to grant it.")

    @staticmethod
    def _item(conn, person, item_id):
        try:
            item_id = int(item_id)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Expected a numeric item identifier") from e
        row = conn.execute("select role, assignee, subjects from work_item where id = %s", (item_id,)).fetchone()
        if row and not access.may_act_on(conn, person, row["subjects"]):
            raise HTTPException(403, AREA_REFUSAL)
        # a case given to someone by name is theirs to act on, whatever queue it last sat in: the field hands a job
        # back to the IOC person who sent it while the case still names the operator queue
        if row and row["role"] and row["assignee"] != person.actor and not _may_work(conn, person.actor, row["role"]):
            raise HTTPException(403, "This case needs its assigned workflow role")
        if (row and row["assignee"] and row["assignee"] != person.actor and not person.allows("configure")
                and not _leads(conn, person.actor, row["role"])):
            raise HTTPException(403, "This case is assigned to another person")

    @staticmethod
    def _exception(conn, person, exception_id):
        try:
            exception_id = int(exception_id)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Expected a numeric exception identifier") from e
        row = conn.execute("select subjects from exception_record where id = %s", (exception_id,)).fetchone()
        if row and not access.may_act_on(conn, person, row["subjects"]):
            raise HTTPException(403, AREA_REFUSAL)

    @staticmethod
    def _task(conn, person, task_id):
        try:
            task_id = int(task_id)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Expected a numeric task identifier") from e
        row = conn.execute("select t.role, t.kind, t.assignee, i.subjects from work_token t "
                           "left join work_item i on i.id = t.item_id where t.id = %s", (task_id,)).fetchone()
        if row and not access.may_act_on(conn, person, row["subjects"] or []):
            raise HTTPException(403, AREA_REFUSAL)
        if (row and row["kind"] != "agent" and row["role"] and row["assignee"] != person.actor
                and not _may_work(conn, person.actor, row["role"])):
            raise HTTPException(403, "This step needs its assigned workflow role")
        if row and row["kind"] == "agent" and not person.allows("configure"):
            raise HTTPException(403, "An administrator must hand off automated steps before a person takes over")


def _may_work(conn, actor: str, role: str) -> bool:
    """Whether someone may act on work in a role's queue: they hold the role, the IOC lead role, or the role a site has
    the queue escalate to. The IOC lead can close a job that came back from the field whatever queue its step names,
    though no queue escalates to it by default (#1369)."""
    from ..workflow.definitions import roles_of
    from ..workflow.roles import LEAD

    held = roles_of(conn, actor)
    if role in held or LEAD in held:
        return True
    up = conn.execute("select escalate_to from work_role where name = %s", (role,)).fetchone()
    return bool(up and up["escalate_to"] and up["escalate_to"] in held)


def _leads(conn, actor: str, role: str | None) -> bool:
    """Whether someone may act on a case another person holds: it sits in a lead's queue (the IOC lead's, or one a
    site has other queues escalate to) and they hold that role. Leads cover for each other across a shift, so the IOC
    lead who sent a job to the field can close it out when it comes back to another lead. A job in the field stays
    with whoever is doing it."""
    from ..workflow.definitions import roles_of
    from ..workflow.roles import LEAD

    return bool(role) and role in roles_of(conn, actor) and (role == LEAD or bool(
        conn.execute("select 1 from work_role where escalate_to = %s limit 1", (role,)).fetchone()))


AREA_REFUSAL = ("This work is on plant outside your areas. You can still read it; ask an owner to add the area, "
                "or hand it to its area's queue.")
PROJECT_HEADER = "x-fuzer-project"
PROJECT_QUERY = "fuzer_project"  # links the browser opens itself (a document, a full-size figure) can't send headers


def _scrubbed(response, hidden: frozenset[str]):
    """A JSON response without the restricted documents in it; a response that is one of them answers 404."""
    if not isinstance(response, JSONResponse) and getattr(response, "media_type", None) != "application/json":
        return response
    body = getattr(response, "body", None)
    if body is None:  # a streamed response: not JSON this route builds
        return response
    data = json.loads(body)
    if access.refers_to(data, hidden):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(access.scrub(data, hidden), status_code=response.status_code, headers=headers)


def requested_project(request) -> str | None:
    """The project a request asks for: the X-Fuzer-Project header, or on a GET the fuzer_project query value."""
    value = request.headers.get(PROJECT_HEADER, "")
    if not value and request.method in ("GET", "HEAD"):
        value = request.query_params.get(PROJECT_QUERY, "")
    return value.strip() or None


class ProjectScope:
    """Runs each request for the project the UI picked (the X-Fuzer-Project header), so every connection it opens
    searches that project's schema. No header: the server's own project. The project must be one this database
    holds; whether this person may open it is checked by AuthenticatedRoute once they are identified."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        name = requested_project(Request(scope))
        if name is None:
            return await self.app(scope, receive, send)
        project = await run_in_threadpool(self._resolve, name)
        if project is None:
            response = JSONResponse({"detail": f"No project named {name!r} in this instance"}, status_code=404)
            return await response(scope, receive, send)
        with db.use(project):
            await self.app(scope, receive, send)

    @staticmethod
    def _resolve(name: str) -> db.Scope | None:
        from ..core import projects

        with db.connect() as conn:
            if name not in projects.stored(conn):
                return None
            return projects.scope(conn, name)


class RequestBounds:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > MAX_BODY:
                raise HTTPException(413, "Request exceeds the 32 MB upload limit")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"x-content-type-options", b"nosniff"),
                                (b"referrer-policy", b"no-referrer"),
                                (b"x-frame-options", b"SAMEORIGIN")])
                if not scope["path"].startswith("/assets/"):
                    headers.append((b"cache-control", b"no-store"))
                elif HASHED_ASSET.search(scope["path"]):  # a new build gives it a new name: never ask again (#1069)
                    headers.append((b"cache-control", b"public, max-age=31536000, immutable"))
                message["headers"] = headers
            await send(message)
        await self.app(scope, bounded_receive, secure_send)

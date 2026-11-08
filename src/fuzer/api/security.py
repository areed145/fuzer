"""One HTTP access boundary, including routes that read settings or trigger work on GET."""

import functools
import hmac
import inspect
import json
from contextvars import ContextVar

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from ..core import access, db, settings

current: ContextVar[access.Principal | None] = ContextVar("fuzer_http_principal", default=None)
mutating: ContextVar[bool] = ContextVar("fuzer_http_mutating", default=False)
MAX_BODY = 32 * 1024 * 1024  # Four existing 5 MB assistant images, base64 encoded, plus JSON.
_active = 0


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
        if (path.startswith(("/work/definitions", "/work/forms", "/work/roles", "/work/people"))
                or path == "/work/tick" or path.endswith("/migrate")):
            return "configure"
        return "operate"
    return "read"


def _arguments(kwargs):
    """Bind audit actors after validation; do not rewrite nested workflow data or read sort parameters."""
    person = current.get()
    if person is None or not mutating.get():
        return
    for key, value in kwargs.items():
        if isinstance(value, BaseModel) and "by" in type(value).model_fields:
            setattr(value, "by", person.actor)
    if "by" in kwargs:
        kwargs["by"] = person.actor


class AuthenticatedRoute(APIRoute):
    def __init__(self, path, endpoint, **kwargs):
        # include_router reconstructs routes, so wrap each endpoint only once.
        if not getattr(endpoint, "_trusted_actor", False):
            original = endpoint
            if inspect.iscoroutinefunction(original):
                @functools.wraps(original)
                async def endpoint(**values):
                    _arguments(values)
                    return await original(**values)
            else:
                @functools.wraps(original)
                def endpoint(**values):
                    _arguments(values)
                    return original(**values)
            endpoint._trusted_actor = True
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request):
            global _active
            if self.path in ("/", "/health"):
                return await handler(request)
            person = await run_in_threadpool(identify, request)
            if person is None:
                return await handler(request)
            if db.scope().project and requested_project(request):
                await run_in_threadpool(self._project_access, person)
            require(person, permission(self.path, request.method))
            unsafe = request.method not in ("GET", "HEAD")
            if unsafe:
                csrf_check(request, person)
            # Some existing GET previews can call a provider. Treat those explicitly as paid execution.
            try:
                claude = TypeAdapter(bool).validate_python(request.query_params.get("claude", "false"))
            except ValidationError as e:
                raise HTTPException(422, "claude must be a boolean") from e
            if (self.path == "/assistant/ask" or self.path.endswith(("/generate", "/remember"))
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
            limit = await run_in_threadpool(settings.get, None, "access.api_concurrency")
            if _active >= limit:
                raise HTTPException(429, "The server is busy. Wait briefly and try again.", headers={"Retry-After": "2"})
            _active += 1
            token = current.set(person)
            mutation = mutating.set(unsafe)
            try:
                return await handler(request)
            finally:
                current.reset(token)
                mutating.reset(mutation)
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
            if self.path == "/work/tasks/bulk-complete":
                for task_id in getattr(request.state, "task_ids", []):
                    self._task(conn, person, task_id)

    @staticmethod
    def _project_access(person: access.Principal):
        with db.connect() as conn:
            if db.scope().project not in access.projects_for(conn, person):
                raise HTTPException(403, "You don't have access to this project. Ask an owner to grant it.")

    @staticmethod
    def _item(conn, person, item_id):
        from ..workflow.definitions import roles_of
        try:
            item_id = int(item_id)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Expected a numeric item identifier") from e
        row = conn.execute("select role, assignee from work_item where id = %s", (item_id,)).fetchone()
        if row and row["role"] and row["role"] not in roles_of(conn, person.actor):
            raise HTTPException(403, "This case needs its assigned workflow role")
        if row and row["assignee"] and row["assignee"] != person.actor and not person.allows("configure"):
            raise HTTPException(403, "This case is assigned to another person")

    @staticmethod
    def _task(conn, person, task_id):
        from ..workflow.definitions import roles_of
        try:
            task_id = int(task_id)
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "Expected a numeric task identifier") from e
        row = conn.execute("select role, kind, assignee from work_token where id = %s", (task_id,)).fetchone()
        if row and row["kind"] != "agent" and row["role"] and row["role"] not in roles_of(conn, person.actor):
            raise HTTPException(403, "This step needs its assigned workflow role")
        if row and row["kind"] == "agent" and not person.allows("configure"):
            raise HTTPException(403, "An administrator must hand off automated steps before a person takes over")


PROJECT_HEADER = "x-fuzer-project"
PROJECT_QUERY = "fuzer_project"  # links the browser opens itself (a document, a full-size figure) can't send headers


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
                message["headers"] = headers
            await send(message)
        await self.app(scope, bounded_receive, secure_send)

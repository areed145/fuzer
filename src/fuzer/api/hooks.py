"""Webhooks (#465): another system sends work without a Fuzer client or a sign-in.

    POST /hooks/{system}      header X-Fuzer-Secret: the shared secret the system's webhook_secret refers to
    {"type": "short_gas_day", "key": "MR-801:2026-11-09", "title": "...", "refs": ["tag:H1-FT-8010"], ...}
    {"type": "short_gas_day", "key": "MR-801:2026-11-09", "clear": true, "reason": "flow back"}

The body is the `POST /exceptions` payload (or a clear), for the exception types the system brought. These routes
are authenticated by the secret, not a session, so they are the one router outside AuthenticatedRoute besides
/auth: a missing or wrong secret is refused before the body is read.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .deps import get_conn

router = APIRouter(prefix="/hooks")


class HookCall(BaseModel):
    type: str
    key: str = Field(min_length=1, max_length=500)
    title: str | None = Field(None, max_length=500)
    refs: list[str] = Field(default_factory=list, max_length=50)
    priority: float | None = Field(None, ge=0, le=1)
    value_usd: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    clear: bool = False
    reason: str | None = Field(None, max_length=500)


def _secret(system: str, x_fuzer_secret: str | None = Header(None), conn=Depends(get_conn)):
    """Checked before the body: a caller without the secret learns nothing about the payload it would take."""
    from ..sources import workflows

    try:
        workflows.webhook_auth(conn, system, x_fuzer_secret)
    except workflows.WebhookError as e:
        raise HTTPException(401, str(e)) from e
    return conn


@router.post("/{system}")
def hook(system: str, call: HookCall, conn=Depends(_secret)):
    """Raise (or clear) one of the system's exceptions."""
    from ..core.exceptions import ExceptionError
    from ..core.jsonsafe import plain
    from ..sources import workflows

    if not call.clear and not call.title:
        raise HTTPException(422, "a raise needs a title")
    try:
        out = workflows.webhook(conn, system, call.model_dump())
    except ExceptionError as e:
        conn.rollback()
        raise HTTPException(400, str(e)) from e
    conn.commit()
    return plain(out)

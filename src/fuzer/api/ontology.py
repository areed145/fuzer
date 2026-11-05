"""The Ontology page (#241): the data model, declared and observed, and editing it. Every change is audited
with who made it and why."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import ontology
from ..core.ontology import OntologyError
from ..graph import ontology as model
from .deps import get_conn

router = APIRouter()


@router.get("/ontology")
def ontology_model(conn=Depends(get_conn)):
    """Kinds and links as the register holds them, with what the ontology declares and what needs a definition."""
    return model.model(conn)


@router.get("/ontology/declared")
def ontology_declared(conn=Depends(get_conn)):
    """Only the declared model (for pickers): groups, views, kinds, link types."""
    return ontology.declared(conn)


@router.get("/ontology/history")
def ontology_history(object: str | None = None, name: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    return ontology.history(conn, object, name, limit)


class KindIn(BaseModel):
    name: str | None = None
    label: str | None = None
    description: str | None = None
    group: str | None = None
    colour: str | None = None
    label_field: str | None = None
    status: str | None = None
    by: str = "anonymous"
    reason: str | None = None


class RelationIn(BaseModel):
    name: str | None = None
    label: str | None = None
    description: str | None = None
    view: str | None = None
    dated: bool | None = None
    from_kinds: list[str] | None = None
    to_kinds: list[str] | None = None
    status: str | None = None
    by: str = "anonymous"
    reason: str | None = None


def _values(body: BaseModel) -> dict:
    return body.model_dump(exclude_unset=True, exclude={"name", "by", "reason"})


def _run(f, *a, **kw):
    try:
        return f(*a, **kw)
    except OntologyError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/ontology/kinds")
def kind_add(body: KindIn, conn=Depends(get_conn)):
    if not body.name:
        raise HTTPException(400, "a kind needs a name")
    return _run(ontology.add_kind, conn, body.name, body.by, body.reason, **_values(body))


@router.put("/ontology/kinds/{name}")
def kind_update(name: str, body: KindIn, conn=Depends(get_conn)):
    if body.name and body.name != name:
        _run(model.rename_kind, conn, name, body.name, body.by, body.reason)
        name = body.name
    return _run(ontology.update_kind, conn, name, body.by, body.reason, **_values(body))


@router.delete("/ontology/kinds/{name}")
def kind_delete(name: str, by: str = "anonymous", reason: str | None = None, conn=Depends(get_conn)):
    return _run(ontology.delete_kind, conn, name, by, reason)


@router.post("/ontology/relations")
def relation_add(body: RelationIn, conn=Depends(get_conn)):
    if not body.name:
        raise HTTPException(400, "a link type needs a name")
    return _run(ontology.add_relation, conn, body.name, body.by, body.reason, **_values(body))


@router.put("/ontology/relations/{name}")
def relation_update(name: str, body: RelationIn, conn=Depends(get_conn)):
    return _run(ontology.update_relation, conn, name, body.by, body.reason, **_values(body))


@router.delete("/ontology/relations/{name}")
def relation_delete(name: str, by: str = "anonymous", reason: str | None = None, conn=Depends(get_conn)):
    return _run(ontology.delete_relation, conn, name, by, reason)

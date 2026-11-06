"""Sources capabilities, declared with `capability` (#133)."""

from typing import Any, Literal
from ...core import db
from .. import mcp_server
from ..mcp_server import _plain, _resolve, capability


@capability("all", kind="query")
def search_knowledge(question: str, ref: str | None = None, limit: int = 8,
                     include_superseded: bool = False, failure_modes: list[str] | None = None) -> dict[str, Any]:
    """Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited
    passages (document, revision, page, section, snippet with [[matches]]).

    ref scopes the search to what applies to one tag / equipment / well: the item itself, its model's
    manuals, its class's procedures and its area's guides, ranked closest first. Always cite the document
    and page you use. Superseded revisions are left out unless include_superseded is true. failure_modes
    (ISO 14224 codes such as VIB, ELP, FTS, OHE) boosts passages about those modes."""
    from ...graph import knowledge
    with db.connect() as conn:
        fid = None
        if ref:
            fid, err = _resolve(conn, ref)
            if err:
                return err
        return _plain({"for": fid or "facility",
                       "passages": knowledge.search(conn, question, fid, min(limit, 30), include_superseded,
                                                    failure_modes)})


@capability("all", kind="query")
def documents_for(ref: str) -> dict[str, Any]:
    """Current documents that apply to a tag / equipment / model / class: its OEM manual (via model), class
    procedures and area troubleshooting guides, closest first, with why each applies."""
    from ...graph import knowledge
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        return _plain({"for": fid, "documents": knowledge.documents_for(conn, fid)})


@capability("field-operations-facilities", "maintenance-integrity", "hse-incident-management", kind="query")
def lessons_learned(status: Literal["approved", "suggested", "all"] = "approved", limit: int = 20) -> dict[str, Any]:
    """Lessons learned per equipment model (or class) and ISO 14224 failure mode: the corrective work that has
    fixed it before, how often it held for 30 days, and the incidents behind it. Approved lessons were
    reviewed by engineers; suggested ones are drafts. Approved lessons also come back from search_knowledge."""
    from ...graph import lessons
    with db.connect() as conn:
        return _plain({"lessons": lessons.listing(conn, None if status == "all" else status, min(limit, 200))})


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="query"
)
def measurements(ref: str, quantity: str | None = None, since: str | None = None, until: str | None = None,
                 history: bool = False) -> dict[str, Any]:
    """Sparse measurements that aren't in PI, on a well, tag, equipment, pipeline or an incident's unit:
    well tests (oil_rate, water_cut, gor, lift_gas_rate, wellhead_pressure), lab results at sample points
    (bsw, salt, density, oil_in_water, h2s, co2, water_dewpoint, chloride) and wall thickness at CMLs
    (wall_thickness, mm). One series per quantity and location with the latest value, min / max, the
    change per year (wall loss rate, decline) and the last three readings; history=true returns every
    reading. A qualifier "<" means below the detection limit. Dates YYYY-MM-DD."""
    from ...graph import measurements as m
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        if history:
            return _plain(m.for_entity(conn, fid, quantity, since, until))
        out = m.for_entity(conn, fid, quantity, since, until)
    out["series"] = [{k: v for k, v in s.items() if k != "values"} | {"recent": s["values"][-3:]}
                     for s in out["series"]]
    return _plain(out)


@capability(
    "production-surveillance", "wells-subsurface", "field-operations-facilities", "maintenance-integrity",
    kind="calc"
)
def equipment_curves(ref: str) -> dict[str, Any]:
    """The OEM curves (pump head / efficiency / NPSHr / power against flow, or choke Cv against opening)
    and ratings (BEP, operating regions, max Cv) for an equipment model, or the model of an equipment
    item or tag, and where they came from (vendor export or an accepted datasheet reading)."""
    from ...sources import curves
    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        model = curves.model_of(conn, fid)
        out = curves.for_model(conn, model) if model else None
        return _plain(out or {"model_id": model, "series": {}, "waiting": curves.waiting(conn, model) if model else []})


@capability("all", kind="query")
def source_health(dataset: str | None = None) -> list[dict[str, Any]]:
    """How each source dataset is doing (#225): state (ok, stale, held, failed, checks failing, profile drift),
    when it last loaded against how often it should, failing checks, link coverage and open reviews of its
    source. Check this before an answer rests on data that may be stale or broken.

    dataset optionally limits it to one (sap_equipment, pi_points, oplogs, ...).
    """
    from ...sources import quality

    with db.connect() as conn:
        return quality.health(conn, dataset)


@capability("all", kind="query")
def why_value(ref: str, field: str) -> dict[str, Any]:
    """Why an entity's field says what it says (#225): the rule that chose the value, and each source record
    that states one, with the mapping version that read it and the load that delivered it."""
    from ...sources import impact

    with db.connect() as conn:
        fid, err = _resolve(conn, ref)
        if err:
            return err
        try:
            return impact.why(conn, mcp_server.PACK, fid, field)
        except impact.ImpactError as e:
            return {"error": str(e)}

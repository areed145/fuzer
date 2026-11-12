"""Detection providers (P3.8, #703): the anomaly detection and predictive health tools an operator already runs
(vendor condition monitoring, an equipment-specific predictive model, leak detection, corrosion or well
surveillance), their alerts mapped to the finding contract and raised as work, so they join Fuzer's own detections
on the same event instead of opening a second case.

A source pack declares them beside its work types:

    exception_types:
      - {name: health_alert, label: Predictive health alert, role: role:engineer}
    detection_providers:
      - name: healthwatch
        label: HealthWatch predictive health
        type: health_alert                                   the work type its alerts raise (joins, as any type)
        key: "{alert_id}"                                    the provider's own identity for the alert
        refs: ["tag:{asset_tag}", {from: serial, as: tag}]   what it is about, as exception rules write refs
        title: "{asset_tag}: {message}"
        severity: {from: severity, map: {critical: 1.0, high: 0.8, medium: 0.5, low: 0.2}}
        confidence: {from: probability, scale: 100}          0 to 1 once divided by `scale` (default 1)
        start: "{detected_at}"
        link: "{url}"                                        the provider's own page for it (http or https)
        items: [{label: Remaining useful life, from: rul_days, unit: d}]
        clear_when: {status: {in: [closed, resolved]}}       what clears it (none: alerts only raise)

The provider sends its alerts as it writes them, one object or a list, to `POST /hooks/{system}/alerts` behind the
system's webhook secret (`?provider=` when the system has more than one). Each becomes an external finding named for
the provider, with its severity, confidence and link, and goes through the exception contract, so a Fuzer detection
open on the same asset hosts it (`ops.exceptions._host`). An alert whose asset reference matches nothing in the
register is never dropped: it goes to the data steward's queue with its references kept, to match.
"""

import re

from psycopg.types.json import Jsonb

from ..core import exceptions as xc
from ..core.findings import EXTERNAL, Finding, FindingError, at, fact, link
from ._catalog_common import CatalogError
from .workflows import _check_refs, _check_when, _refs, fill, meets

FIELDS = ("label", "type", "key", "refs", "title", "severity", "confidence", "start", "link", "items", "clear_when",
          "enabled")
STEWARD = "role:data_steward"  # where an alert about an asset the register doesn't know goes
MAX_ALERTS = 500  # in one call


class ProviderError(ValueError):
    """An alert the provider's mapping can't turn into work (no key, no title)."""


def check(p: dict) -> dict:
    """A provider entry from a pack, checked: its mapping as stored."""
    name = p.get("name") if isinstance(p, dict) else None
    if not (isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name)):
        raise CatalogError("a detection provider needs a name: lower case letters, digits and _")
    if unknown := set(p) - {"name", *FIELDS}:
        raise CatalogError(f"detection provider {name}: unknown field(s) {sorted(unknown)}; one of {list(FIELDS)}")
    for need in ("label", "type", "key", "refs", "title"):
        if not p.get(need):
            raise CatalogError(f"detection provider {name}: {need} is required")
    sev = p.get("severity")
    if sev is not None and not (isinstance(sev, dict) and sev.get("from") and isinstance(sev.get("map", {}), dict)
                                and all(isinstance(v, int | float) and 0 <= v <= 1 for v in sev.get("map", {}).values())):
        raise CatalogError(f"detection provider {name}: severity is {{from: field, map: {{value: priority 0-1}}}}")
    conf = p.get("confidence")
    if conf is not None and not (isinstance(conf, dict) and conf.get("from")
                                 and isinstance(conf.get("scale", 1), int | float) and conf.get("scale", 1) > 0):
        raise CatalogError(f"detection provider {name}: confidence is {{from: field, scale: number}}")
    items = p.get("items") or []
    if not all(isinstance(i, dict) and i.get("label") and i.get("from") for i in items):
        raise CatalogError(f"detection provider {name}: each item is {{label, from, unit}}")
    return {"key": str(p["key"]), "refs": _check_refs(p["refs"]), "title": str(p["title"]),
            **({"severity": {"from": sev["from"], "map": {str(k).lower(): v for k, v in (sev.get("map") or {}).items()}}}
               if sev else {}),
            **({"confidence": {"from": conf["from"], "scale": conf.get("scale", 1)}} if conf else {}),
            **{k: str(p[k]) for k in ("start", "link") if p.get(k)},
            **({"items": [{"label": i["label"], "from": i["from"], **({"unit": i["unit"]} if i.get("unit") else {})}
                          for i in items]} if items else {}),
            **({"clear_when": _check_when(p["clear_when"])} if p.get("clear_when") else {})}


def set_provider(conn, system: str, p: dict, by: str) -> dict:
    """Add or change one of a system's providers. Its work type must exist (a pack brings it in exception_types)."""
    from . import _catalog_entities as entities

    mapping = check(p)
    if not entities.system(conn, system):
        raise CatalogError(f"no system {system!r}")
    if p["type"] not in xc.types(conn):
        raise CatalogError(f"detection provider {p['name']}: no exception type {p['type']!r}; bring it in "
                           "exception_types")
    return dict(conn.execute(
        """
        insert into detection_provider (system, name, label, type, mapping, enabled, updated_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (system, name) do update set label = excluded.label, type = excluded.type,
            mapping = excluded.mapping, enabled = excluded.enabled, updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (system, p["name"], str(p["label"]), p["type"], Jsonb(mapping), p.get("enabled", True), by)).fetchone())


def providers(conn, system: str | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "select * from detection_provider where (%(s)s::text is null or system = %(s)s) order by system, name",
        {"s": system})]


def as_pack(p: dict) -> dict:
    """A provider as a pack entry."""
    return {"name": p["name"], "label": p["label"], "type": p["type"], **p["mapping"],
            **({} if p["enabled"] else {"enabled": False})}


def _provider(conn, system: str, name: str | None) -> dict:
    mine = [p for p in providers(conn, system) if p["enabled"]]
    if name:
        mine = [p for p in mine if p["name"] == name]
    if len(mine) != 1:
        raise xc.ExceptionError(f"{system} has {'no provider ' + repr(name) if name else f'{len(mine)} providers'}"
                                + ("" if name or not mine else "; name one with ?provider="))
    return mine[0]


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def finding(p: dict, alert: dict) -> tuple[Finding, float | None]:
    """An alert as an external finding named for its provider, and the priority its severity gives."""
    m = p["mapping"]
    sev = m.get("severity")
    said = alert.get(sev["from"]) if sev else None
    priority = sev["map"].get(str(said).lower()) if sev and said is not None else None
    conf = m.get("confidence")
    c = _number(alert.get(conf["from"])) if conf else None
    c = None if c is None else max(0.0, min(1.0, c / conf["scale"]))
    url = fill(m["link"], alert) if m.get("link") else None
    try:
        evidence = link("Provider's evidence", url, f"Open in {p['label']}")
    except FindingError:
        evidence = fact("Provider's evidence", url)
    start = fill(m["start"], alert) if m.get("start") else None
    items = (fact("Reported by", p["label"]), fact("Severity", None if said is None else str(said)),
             at("Detected", start), evidence,
             *(fact(i["label"], alert.get(i["from"]), i.get("unit")) for i in m.get("items") or []))
    return Finding(f"provider.{p['name']}", EXTERNAL, items=items, start=start or None, confidence=c,
                   method=f"{p['label']}'s own detection", data={"provider": p["name"], "alert": alert}), priority


def _one(conn, system: str, p: dict, alert: dict) -> dict:
    from ..core import pack as packs

    m = p["mapping"]
    key = fill(m["key"], alert).strip()
    if not key:
        raise ProviderError(f"an alert with no {m['key']} has no key")
    by = f"{system} provider {p['name']}"
    if m.get("clear_when") and meets(m["clear_when"], alert):
        out = xc.clear_exception(conn, p["type"], key, by, reason=f"{p['label']} closed it")
        return {"key": key, "cleared": bool(out)}
    title = fill(m["title"], alert).strip()
    if not title:
        raise ProviderError(f"alert {key}: its title template gives nothing")
    refs = _refs(m["refs"], alert, packs.resolve(conn))
    found, priority = finding(p, alert)
    subjects, unmatched = xc.match(conn, refs)
    lost = not subjects  # the register doesn't know the asset: the steward matches it, nothing is dropped
    row = xc.raise_exception(conn, p["type"], key, title, by, refs=refs, priority=priority, finding=found,
                             source=system, **({"role": STEWARD} if lost else {}))
    return {"key": key, "exception_id": row.get("id"), "item_id": row.get("item_id"), "new": row.get("raised"),
            "joined": row.get("joined"), "state": row.get("state"), "unmatched": unmatched,
            **({"to_steward": True} if lost else {})}


def alerts(conn, system: str, body, provider: str | None = None) -> dict:
    """Alerts a provider sent (one object, or a list): each raised, refreshed or cleared. One alert the mapping
    can't read is reported and the rest still go in."""
    p = _provider(conn, system, provider)
    batch = body if isinstance(body, list) else [body]
    if len(batch) > MAX_ALERTS:
        raise xc.ExceptionError(f"at most {MAX_ALERTS} alerts in one call")
    out, errors = [], []
    for a in batch:
        if not isinstance(a, dict):
            errors.append("an alert is a JSON object")
            continue
        try:  # the work engine commits each case it opens, so one bad alert can't take the others with it
            out.append(_one(conn, system, p, a))
        except ProviderError as e:  # read before anything is written
            errors.append(str(e))
        except xc.ExceptionError as e:
            conn.rollback()
            errors.append(str(e))
    return {"provider": p["name"], "alerts": out, "errors": errors}

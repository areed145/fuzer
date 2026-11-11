"""The work a system brings (#465): its exception types, the rules that turn its loaded records into exceptions,
and a webhook through which it can send exceptions itself.

A source pack (or the Sources page) declares, per system:

    exception_types:
      - {name: short_gas_day, label: Short gas day, role: role:production_engineer, audience: [ioc]}
    exception_rules:
      - name: short_day
        dataset: daily_volumes
        type: short_gas_day
        when: {flow_hours: {lt: 23}, status: {ne: E}}     every condition holds
        key: "{meter_id}:{gas_day}"                          default: the record's source key
        refs: [{from: meter, as: tag_or_well}, "sap/{meter_id}"]
        title: "Short gas day on {meter}: {flow_hours} h"
        priority: 0.6
        value_usd: 12000                                      a number, or "{column}"
        clear_when: {flow_hours: {ge: 23.5}}                  optional: what clears it (default: no longer `when`)

Types are defined through the exception contract (fuzer.core.exceptions), as data, marked with the system that
brought them. Rules run after every load over the dataset's current records: a record that meets the rule raises
(or refreshes) its exception; an open exception the rule raised clears when its record meets `clear_when` (or, with
none, no longer meets `when`), or is gone.

The webhook (`POST /hooks/{system}`, header `X-Fuzer-Secret`) takes the `POST /exceptions` payload for the types
the system brought, so a SCADA, LIMS or inspection system sends work without a Fuzer client. The system holds only
a reference to the shared secret (`webhook_secret: env:NAME`), never the value.
"""

import hmac
import re

from psycopg.types.json import Jsonb

from ..core import exceptions as xc
from ._catalog_common import CatalogError

OPS = ("eq", "ne", "in", "not_in", "gt", "ge", "lt", "le", "present", "absent", "contains")
TYPE_FIELDS = ("label", "role", "process", "case_type", "audience", "close_case_on_clear", "description", "joins",
               "due_hours")
RULE_FIELDS = ("dataset", "type", "when", "clear_when", "key", "refs", "title", "priority", "value_usd", "enabled")
BY = "source rules"
_FIELD = re.compile(r"\{([^{}]+)\}")


class WebhookError(PermissionError):
    """A webhook call refused: no webhook for the system, or the wrong secret."""


# ---- exception types ---------------------------------------------------------------------------------------------

def define_types(conn, system: str, types: list[dict], by: str) -> list[str]:
    """Define (or update) the types a system brings. Returns the names changed."""
    have = {t["name"]: t for t in xc.defined_types(conn)}
    changed = []
    for t in types or []:
        if not isinstance(t, dict) or not t.get("name"):
            raise CatalogError("each exception type needs a name")
        if unknown := set(t) - {"name", *TYPE_FIELDS}:
            raise CatalogError(f"exception type {t['name']}: unknown field(s) {sorted(unknown)}")
        old = have.get(t["name"])
        if old and old.get("source_system") not in (None, system):
            raise CatalogError(f"exception type {t['name']} belongs to system {old['source_system']!r}")
        want = {k: t[k] for k in TYPE_FIELDS if k in t}
        want["audience"] = tuple(want.get("audience") or ("ioc",))
        if want.get("role"):
            want["role"] = "role:" + str(want["role"]).removeprefix("role:")
        if old and old.get("source_system") == system and all(
                (tuple(old[k]) if k == "audience" else old[k]) == want.get(k, _DEFAULTS.get(k))
                for k in TYPE_FIELDS):
            continue
        try:
            xc.define_type(conn, t["name"], by, want.pop("label", None) or "", source_system=system, **want)
        except xc.ExceptionError as e:
            raise CatalogError(f"exception type {t['name']}: {e}") from e
        changed.append(t["name"])
    return changed


_DEFAULTS = {"role": None, "process": None, "case_type": None, "audience": ("ioc",), "close_case_on_clear": True,
             "description": None, "joins": True, "due_hours": None}


def types_of(conn, system: str) -> list[dict]:
    """The types a system brought, each with how many are open."""
    return xc.defined_types(conn, system)


# ---- rules -------------------------------------------------------------------------------------------------------

def _check_when(when) -> dict:
    if when in (None, {}):
        return {}
    if not isinstance(when, dict):
        raise CatalogError("when is a map of column -> condition, e.g. {flow_hours: {lt: 23}}")
    out = {}
    for col, cond in when.items():
        if not isinstance(cond, dict):
            cond = {"eq": cond}
        if bad := set(cond) - set(OPS):
            raise CatalogError(f"when {col}: unknown condition(s) {sorted(bad)}; one of {list(OPS)}")
        if any(op in ("in", "not_in") and not isinstance(v, list) for op, v in cond.items()):
            raise CatalogError(f"when {col}: in and not_in take a list")
        out[str(col)] = cond
    return out


def _check_refs(refs) -> list:
    from .extract import RESOLVERS

    out = []
    for r in refs or []:
        if isinstance(r, str):
            out.append(r)
        elif isinstance(r, dict) and r.get("from"):
            if r.get("as") and r["as"] not in RESOLVERS:
                raise CatalogError(f"refs: unknown resolver {r['as']!r}; one of {sorted(RESOLVERS)}")
            out.append({"from": r["from"], **({"as": r["as"]} if r.get("as") else {})})
        else:
            raise CatalogError("a ref is a template (tag:{meter}, sap/{id}) or {from: column, as: resolver}")
    return out


def set_rule(conn, system: str, name: str, by: str, **values) -> dict:
    """Add or change one of a system's rules (see the module docstring for the fields)."""
    from . import _catalog_entities as entities

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name or ""):
        raise CatalogError("a rule name is lower case letters, digits and _")
    if unknown := set(values) - set(RULE_FIELDS):
        raise CatalogError(f"rule {name}: unknown field(s) {sorted(unknown)}; one of {list(RULE_FIELDS)}")
    if not entities.system(conn, system):
        raise CatalogError(f"no system {system!r}")
    d = entities.dataset(conn, values.get("dataset") or "")
    if d is None or d["source"] != system:
        raise CatalogError(f"rule {name}: {system} has no dataset {values.get('dataset')!r}")
    if values.get("type") not in xc.types(conn):
        raise CatalogError(f"rule {name}: no exception type {values.get('type')!r}; bring it in exception_types")
    p = values.get("priority")
    if p is not None and not (isinstance(p, int | float) and 0 <= p <= 1):
        raise CatalogError(f"rule {name}: priority is a number from 0 to 1")
    v = values.get("value_usd")
    if v is not None and not isinstance(v, int | float) and not (isinstance(v, str) and _FIELD.fullmatch(v)):
        raise CatalogError(f"rule {name}: value_usd is a number or a column, as {{column}}")
    if not values.get("when"):
        raise CatalogError(f"rule {name}: when says which records raise it, e.g. {{flow_hours: {{lt: 23}}}}")
    config = {"when": _check_when(values.get("when")), "refs": _check_refs(values.get("refs")),
              **({"clear_when": _check_when(values["clear_when"])} if values.get("clear_when") else {}),
              **{k: values[k] for k in ("key", "title", "priority", "value_usd") if values.get(k) is not None}}
    return dict(conn.execute(
        """
        insert into source_exception_rule (system, name, dataset, type, config, enabled, updated_by)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (system, name) do update set dataset = excluded.dataset, type = excluded.type,
            config = excluded.config, enabled = excluded.enabled, updated_by = excluded.updated_by, updated_at = now()
        returning *
        """, (system, name, d["name"], values["type"], Jsonb(config), values.get("enabled", True), by)).fetchone())


def remove_rule(conn, system: str, name: str) -> bool:
    return conn.execute("delete from source_exception_rule where system = %s and name = %s",
                        (system, name)).rowcount > 0


def rules(conn, system: str | None = None, dataset: str | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select * from source_exception_rule where (%(s)s::text is null or system = %(s)s)
          and (%(d)s::text is null or dataset = %(d)s) order by system, name
        """, {"s": system, "d": dataset})]


def as_pack(rule: dict) -> dict:
    """A rule as a pack entry."""
    c = rule["config"]
    out = {"name": rule["name"], "dataset": rule["dataset"], "type": rule["type"]}
    for k in ("when", "clear_when", "key", "refs", "title", "priority", "value_usd"):
        if c.get(k) not in (None, {}, []):
            out[k] = c[k]
    if not rule["enabled"]:
        out["enabled"] = False
    return out


# ---- evaluation --------------------------------------------------------------------------------------------------

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _holds(op: str, have, want) -> bool:
    if op == "present":
        return (have not in (None, "")) == bool(want)
    if op == "absent":
        return (have in (None, "")) == bool(want)
    if have in (None, ""):
        return op in ("ne", "not_in")
    if op in ("gt", "ge", "lt", "le"):
        a, b = _num(have), _num(want)
        if a is None or b is None:
            return False
        return {"gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]
    same = lambda x, y: (_num(x) == _num(y)) if _num(x) is not None and _num(y) is not None else str(x) == str(y)
    if op == "eq":
        return same(have, want)
    if op == "ne":
        return not same(have, want)
    if op == "in":
        return any(same(have, w) for w in want)
    if op == "not_in":
        return not any(same(have, w) for w in want)
    if op == "contains":
        return str(want).lower() in str(have).lower()
    return False


def meets(when: dict, payload: dict) -> bool:
    return all(_holds(op, payload.get(col), want) for col, cond in (when or {}).items() for op, want in cond.items())


def fill(template: str, payload: dict) -> str:
    """A template with {column} replaced by the record's value (empty when it has none)."""
    return _FIELD.sub(lambda m: "" if payload.get(m.group(1)) is None else str(payload.get(m.group(1))), template)


def _refs(spec: list, payload: dict, pack) -> list[str]:
    from .extract import RESOLVERS

    out = []
    for r in spec or []:
        if isinstance(r, str):
            if (v := fill(r, payload)) and not v.endswith((":", "/")):
                out.append(v)
            continue
        v = payload.get(r["from"])
        if v in (None, ""):
            continue
        if not r.get("as"):
            out.append(str(v))
        elif pack is not None and (found := RESOLVERS[r["as"]](str(v), pack)) and found[1]:
            out.append(f"{found[0]}:{found[1]}")
    return out


def evaluate(conn, datasets: list[str] | None = None, by: str = BY) -> dict:
    """Run the enabled rules over the current records of the named datasets (all with rules, when None): raise or
    refresh an exception for each record that meets a rule; clear the open ones it raised whose record meets
    `clear_when` (with none: no longer meets `when`) or is gone. Returns {rule: {raised, refreshed, cleared}}."""
    from ..core import pack as packs
    from . import _catalog_entities as entities

    todo = [r for r in rules(conn) if r["enabled"] and (datasets is None or r["dataset"] in datasets)]
    if not todo:
        return {}
    try:
        pack = packs.current_pack(conn)
    except Exception:  # a project with no pack yet: refs that need a resolver are left out
        pack = None
    out = {}
    for rule in todo:
        d = entities.dataset(conn, rule["dataset"])
        if d is None:
            continue
        c, label = rule["config"], f"{rule['system']}/{rule['name']}"
        produces = list(entities.produces(d))
        seen, held, counts = set(), set(), {"raised": 0, "refreshed": 0, "cleared": 0}
        before = xc.open_keys(conn, rule["type"], label)
        for rec in conn.execute(
            "select source_key, payload from source_record where (source, record_type) in "
            "(select * from unnest(%s::text[], %s::text[]))",
                ([s for s, _ in produces], [t for _, t in produces])).fetchall():
            p = rec["payload"]
            key = fill(c["key"], p) if c.get("key") else rec["source_key"]
            if not key or key in seen:
                continue
            if not meets(c.get("when"), p):
                if c.get("clear_when") and key in before and not meets(c["clear_when"], p):
                    held.add(key)  # between the raise and the clear condition: stays open
                continue
            seen.add(key)
            value = c.get("value_usd")
            if isinstance(value, str):
                value = _num(fill(value, p))
            title = fill(c["title"], p) if c.get("title") else f"{rule['name'].replace('_', ' ')}: {key}"
            row = xc.raise_exception(
                conn, rule["type"], key, title, by, refs=_refs(c.get("refs"), p, pack), priority=c.get("priority"), value_usd=value,
                source=rule["system"], evidence={"rule": label, "dataset": d["name"], "record": rec["source_key"]})
            counts["raised" if row.get("raised") else "refreshed"] += 1
        for key in before - seen - held:
            if xc.clear_exception(conn, rule["type"], key, by, reason=f"the record no longer meets rule {label}"):
                counts["cleared"] += 1
        out[label] = counts
    return out


def after_load(conn, datasets: list[str]) -> dict:
    """The rules for the datasets just loaded; a failing rule never fails a load (it is logged)."""
    import logging

    if not datasets:
        return {}
    try:
        out = evaluate(conn, datasets)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        logging.getLogger(__name__).exception("source exception rules failed")
        return {}


# ---- webhook -----------------------------------------------------------------------------------------------------

def check_secret_ref(ref) -> None:
    if ref not in (None, "") and not (isinstance(ref, str) and ref.startswith("env:") and len(ref) > 4):
        raise CatalogError("webhook_secret must be a reference such as env:NAME, never the value")


def webhook_auth(conn, system: str, secret: str | None) -> None:
    """The caller holds the system's shared secret, or WebhookError."""
    from . import _catalog_entities as entities
    from .connections import ConnectionError_, resolve

    s = entities.system(conn, system)
    ref = (s or {}).get("webhook_secret")
    try:
        expected = resolve(ref) if ref else None
    except ConnectionError_:
        expected = None
    if not s or not expected:
        raise WebhookError(f"no webhook is set up for {system!r}")
    if not secret or not hmac.compare_digest(secret.encode(), expected.encode()):
        raise WebhookError("wrong webhook secret")


def webhook(conn, system: str, body: dict) -> dict:
    """A webhook call already authenticated (webhook_auth): raise (or with `clear: true`, clear) an exception of
    one of the types the system brought."""
    mine = {t["name"] for t in types_of(conn, system)}
    t = body.get("type")
    if t not in mine:
        raise xc.ExceptionError(f"{system} can send only the exception types it brought ({', '.join(sorted(mine)) or 'none'})"
                                f", not {t!r}")
    by = f"{system} webhook"
    if body.get("clear"):
        return xc.clear_exception(conn, t, body["key"], by, reason=body.get("reason")) or {"cleared": None}
    return xc.raise_exception(conn, t, body["key"], body["title"], by, refs=body.get("refs") or [],
                              priority=body.get("priority"), value_usd=body.get("value_usd"),
                              evidence={**(body.get("evidence") or {}), "via": "webhook"}, source=system)

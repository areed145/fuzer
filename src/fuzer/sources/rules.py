"""Survivorship and matching rules per source (#225), in the database instead of the asset pack.

- The default source priority is the systems' rank (source_system.rank: lower wins; `review`, a person's
  decision, always comes first).
- A field rule (source_rule scope `field`) gives one field its own priority and whether a disagreement opens a
  conflict review; the rule named `*` holds the default review_on_conflict.
- A match rule (scope `match`) gives one entity kind its thresholds (auto_accept, review_min).

Seeded from the pack on the first rebuild; a rule nobody edited follows the pack (updated_by `seed`). Every
change is audited (source_audit, object `rule`). The asset-specific parts of the pack (tag and well grammar,
aliases, classification) stay in the pack.
"""

import copy
import dataclasses

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain
from ..core.pack import Pack

SEED = "seed"
FIELD_KEYS = {"priority", "review_on_conflict"}
MATCH_KEYS = {"auto_accept", "review_min"}


class RuleError(ValueError):
    pass


def seed(conn, pack: Pack) -> dict:
    """Rules and ranks from the pack: missing ones added, ones still as seeded brought up to date."""
    out = {"rules": 0, "ranks": 0}
    want = {("field", "*"): {"review_on_conflict": bool(pack.survivorship["default"].get("review_on_conflict"))}}
    for f, r in (pack.survivorship.get("fields") or {}).items():
        want[("field", f)] = {k: v for k, v in r.items() if k in FIELD_KEYS}
    for kind, cfg in (pack.matching or {}).items():
        if isinstance(cfg, dict) and MATCH_KEYS & set(cfg):
            want[("match", kind)] = {k: cfg[k] for k in MATCH_KEYS if k in cfg}
    have = {(r["scope"], r["name"]): r for r in conn.execute("select * from source_rule")}
    for (scope, name), cfg in want.items():
        cur = have.get((scope, name))
        if cur is None:
            conn.execute("insert into source_rule (scope, name, config, updated_by) values (%s, %s, %s, %s)",
                         (scope, name, Jsonb(cfg), SEED))
            out["rules"] += 1
        elif cur["updated_by"] == SEED and cur["config"] != cfg:
            conn.execute("update source_rule set config = %s, updated_at = now() where id = %s", (Jsonb(cfg), cur["id"]))
            out["rules"] += 1
    for i, name in enumerate(s for s in pack.survivorship["default"].get("priority", []) if s != "review"):
        out["ranks"] += conn.execute("update source_system set rank = %s where name = %s and rank is null",
                                     ((i + 1) * 10, name)).rowcount
    return out


def apply(conn, pack: Pack) -> Pack:
    """The pack with the database's survivorship and matching rules laid over it."""
    seed(conn, pack)
    ranked = [r["name"] for r in conn.execute(
        "select name from source_system where enabled and rank is not null order by rank, name")]
    rules = {(r["scope"], r["name"]): r["config"] for r in conn.execute("select scope, name, config from source_rule")}
    surv = copy.deepcopy(pack.survivorship)
    default = surv.setdefault("default", {})
    if ranked:
        default["priority"] = ["review", *ranked]
    default.update(rules.get(("field", "*"), {}))
    fields = surv.setdefault("fields", {})
    for (scope, name), cfg in rules.items():
        if scope == "field" and name != "*":
            fields[name] = {**fields.get(name, {}), **cfg}
    matching = copy.deepcopy(pack.matching)
    for (scope, name), cfg in rules.items():
        if scope == "match":
            matching[name] = {**matching.get(name, {}), **cfg}
    return dataclasses.replace(pack, survivorship=surv, matching=matching)


def listing(conn) -> dict:
    return {"ranks": [plain(dict(r)) for r in conn.execute(
                "select name, rank, enabled from source_system order by rank nulls last, name")],
            "rules": [plain(dict(r)) for r in conn.execute("select * from source_rule order by scope, name")]}


def set_rule(conn, scope: str, name: str, config: dict, by: str, reason: str | None = None) -> dict:
    """Add or change a rule (audited)."""
    from .catalog import _audit

    keys = FIELD_KEYS if scope == "field" else MATCH_KEYS if scope == "match" else None
    if keys is None:
        raise RuleError("scope is field or match")
    if set(config) - keys:
        raise RuleError(f"a {scope} rule takes {sorted(keys)}")
    if "priority" in config and not (isinstance(config["priority"], list) and all(isinstance(x, str) for x in config["priority"])):
        raise RuleError("priority is a list of source names")
    for k in MATCH_KEYS & set(config):
        if not 0 <= float(config[k]) <= 1:
            raise RuleError(f"{k} is between 0 and 1")
    if scope == "match" and {"auto_accept", "review_min"} <= set(config) and config["review_min"] > config["auto_accept"]:
        raise RuleError("review_min must not be above auto_accept")
    old = conn.execute("select * from source_rule where scope = %s and name = %s", (scope, name)).fetchone()
    row = conn.execute(
        "insert into source_rule (scope, name, config, updated_by) values (%s, %s, %s, %s) "
        "on conflict (scope, name) do update set config = excluded.config, updated_by = excluded.updated_by, "
        "updated_at = now() returning *", (scope, name, Jsonb(config), by)).fetchone()
    _audit(conn, by, "rule", {"id": row["id"], "name": f"{scope}:{name}"}, "update" if old else "create",
           old=old["config"] if old else None, new=config, reason=reason)
    return plain(dict(row))


def delete_rule(conn, scope: str, name: str, by: str, reason: str | None = None) -> None:
    from .catalog import _audit

    row = conn.execute("delete from source_rule where scope = %s and name = %s returning *", (scope, name)).fetchone()
    if not row:
        raise RuleError(f"no {scope} rule {name!r}")
    _audit(conn, by, "rule", {"id": row["id"], "name": f"{scope}:{name}"}, "delete", old=row["config"], reason=reason)


def set_rank(conn, system: str, rank: int | None, by: str, reason: str | None = None) -> dict:
    from . import catalog

    return catalog.update_system(conn, system, by, reason, rank=rank)

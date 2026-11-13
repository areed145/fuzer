"""Shared definitions and helpers for the source catalog."""

from psycopg.types.json import Jsonb

from ..core.jsonsafe import plain

SEED = "seed"
LOAD_MODES = ("snapshot", "incremental", "append")
DRIFT_POLICIES = ("hold_any", "hold_breaking", "ignore")
DUPLICATES = ("hold", "keep_latest")  # a repeated key holds the load, or the latest row per key loads (#459)
STATUSES = ("draft", "active", "paused", "retired")
SYSTEM_KINDS = {"sap": "erp", "jde": "erp", "maximo": "erp", "pi": "historian", "pi_af": "historian",
                "adx": "historian", "dcs": "historian", "ec": "allocation", "edms": "documents", "pid": "documents",
                "oem": "documents", "gis": "gis", "lims": "lab", "inspection": "integrity", "iow": "integrity", "ldar": "emissions", "cms": "condition_monitoring",
                "oplog": "logs", "teams": "logs", "cfihos": "documents"}
WHERE = ("location", "connector", "key", "watermark")
CATALOG = ("load_mode", "drift_policy", "duplicates", "status", "schedule", "expected_hours", "owner", "notes")
SYSTEM_FIELDS = ("kind", "description", "owner", "steward", "timezone", "encoding", "decimal", "date_format", "rank",
                 "enabled", "notes", "webhook_secret", "closeout", "closeout_role", "writeback")
CONNECTION_FIELDS = ("name", "type", "config", "secrets", "enabled", "class", "direction")
# Every connection's class and direction (D4, docs/ot-security.md): a control or control-adjacent system is only read.
CLASSES = ("control", "control_adjacent", "business")
DIRECTIONS = ("read", "write")
READ_ONLY = ("control", "control_adjacent")
CONTROL_KINDS = ("historian",)  # systems of these kinds sit next to the control system unless a connection says else
DATASET_FIELDS = ("connection_id", "record_type", "reader", *WHERE, *CATALOG)


class CatalogError(ValueError):
    pass


def zone_default(kind: str | None) -> str:
    """The class a connection to a system of this kind takes when none is given: control-adjacent for a historian or
    a DCS export, business otherwise."""
    return "control_adjacent" if kind in CONTROL_KINDS else "business"


def check_zone(cls: str | None, direction: str | None) -> None:
    """A connection's class and direction are known ones, and a control or control-adjacent one is only read (D4)."""
    if cls not in CLASSES:
        raise CatalogError(f"a connection's class is one of {', '.join(CLASSES)}, not {cls!r}")
    if direction not in DIRECTIONS:
        raise CatalogError(f"a connection's direction is read or write, not {direction!r}")
    if cls in READ_ONLY and direction != "read":
        raise CatalogError(f"a {cls.replace('_', '-')} connection can only be read: Fuzer never writes to the control "
                           "system or what sits next to it")


def _audit(conn, by: str, obj: str, row: dict | None, action: str, old: dict | None = None, new: dict | None = None,
           reason: str | None = None) -> None:
    conn.execute("insert into source_audit (by, object, object_id, name, action, old, new, reason) "
                 "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (by, obj, (row or {}).get("id"), (row or {}).get("name"), action,
                  Jsonb(plain(old)) if old is not None else None, Jsonb(plain(new)) if new is not None else None, reason))


def _changes(old: dict, values: dict, fields: tuple[str, ...]) -> tuple[dict, dict]:
    unknown = set(values) - set(fields)
    if unknown:
        raise CatalogError(f"unknown field(s) {sorted(unknown)}; one of {list(fields)}")
    new = {k: v for k, v in values.items() if old.get(k) != v}
    return {k: old.get(k) for k in new}, new


def _check_dataset(values: dict) -> None:
    for field, allowed in (("load_mode", LOAD_MODES), ("drift_policy", DRIFT_POLICIES), ("duplicates", DUPLICATES),
                           ("status", STATUSES)):
        if field in values and values[field] not in allowed:
            raise CatalogError(f"{field} must be one of {list(allowed)}")
    if "key" in values and isinstance(values["key"], str):
        values["key"] = [values["key"]]
    if values.get("connector"):
        from .mappings import CONNECTORS

        if values["connector"] not in CONNECTORS:
            raise CatalogError(f"unknown connector {values['connector']!r}; one of {list(CONNECTORS)}")
    if values.get("schedule"):
        if len(str(values["schedule"]).split()) != 5:
            raise CatalogError("schedule is a cron expression with five fields, e.g. '0 * * * *'")

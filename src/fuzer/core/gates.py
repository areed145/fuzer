"""Human gates as policy (P5.10, #619): where a person must decide, held as data and checked at the server.

Two parts:

    fixed gates        step kinds that always need a person, whatever the confidence (FIXED). Nothing configures
                       them away: an agent, a webhook or the system calling the API is refused, and the refusal is
                       recorded.
    confidence gates   a threshold per decision kind and asset criticality (CONFIDENCE, overridable as data in
                       `gate_policy`). At or above it the decision proceeds and is logged; below it, with no
                       threshold (always a person), or when the agent says it could not check something, it stops
                       at a person with the reason in plain words.

    require_person(conn, kind, actor, ...)   a fixed gate: raises GateRefused for a machine actor
    check(conn, kind, confidence, ...)       a confidence gate: {proceed, threshold, fired, reason}
    refuse_on_bypass(conn, subjects, actor)  the bypass_safeguard gate on a machine's proposal about equipment
                                             whose safeguard is bypassed (P3.9c): the reason when it is refused
    decisions(conn, item_id)                 every gate decision on a case, so a person sees why it did or didn't
                                             stop for them

Every call records a `gate_decision` row: the kind, the confidence, the threshold applied, the criticality and
whether the gate fired. The defaults are the ones agreed for the operating model: automatic matches at 99%,
automatic closes as duplicate or false alarm at 95%, an agent's proposed disposition or draft always stops at a
person, and anything on a high-criticality (A) asset always stops at a person.
"""

import logging
from dataclasses import dataclass

from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)


class GateRefused(PermissionError):
    """A fixed gate refused a machine actor: the step needs a named person."""

    status = 403


FIXED: dict[str, str] = {
    "close_safety_case": "Closing a safety or environmental case",
    "field_dispatch": "Dispatching people to the field",
    "write_back": "Writing back to a source system",
    "limit_change": "Changing a limit or detector parameter",
    "publish": "Publishing a process, a skill or a lesson",
    "bypass_safeguard": "Bypassing a safeguard",
    "field_start_without_permit": "Starting field work without a valid permit",
}

ALWAYS = None  # a threshold of None: always a person


@dataclass(frozen=True)
class Kind:
    label: str
    default: float | None                 # the threshold for an asset of no listed criticality
    by_criticality: dict                  # criticality (SAP ABC) -> threshold


CONFIDENCE: dict[str, Kind] = {
    "auto_match": Kind("An automatic match", 0.99, {"A": ALWAYS}),
    "auto_close": Kind("An automatic close as duplicate or false alarm", 0.95, {"A": ALWAYS}),
    "agent_disposition": Kind("An agent's proposed disposition", ALWAYS, {"A": ALWAYS}),
    "agent_draft": Kind("An agent's draft", ALWAYS, {"A": ALWAYS}),
}

# The case types whose close is the close_safety_case gate: integrity operating window breaches by default; more
# are added as data (gate_policy row `close_safety_case`, {"case_types": [...]}).
SAFETY_CASE_TYPES = ("iow_breach",)


def machine(actor: str | None) -> bool:
    """Is the actor not a named person: an agent, a detector or source (`kind:name`, other than `user:`), the system
    or a webhook?"""
    a = (actor or "").strip()
    return (not a or a in ("system", "fuzer", "anonymous") or a.endswith(" webhook")
            or (":" in a and not a.startswith("user:")))


def _row(conn, kind: str) -> dict | None:
    return conn.execute("select * from gate_policy where kind = %s", (kind,)).fetchone()


def _record(conn, kind: str, actor: str, *, fired: bool, reason: str, confidence=None, threshold=None,
            criticality=None, ref=None, item_id=None, could_not_check=()) -> dict:
    return conn.execute(
        "insert into gate_decision (kind, actor, ref, item_id, confidence, threshold, criticality, could_not_check, "
        "fired, reason) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (kind, actor, ref, item_id, confidence, threshold, criticality, list(could_not_check), fired, reason)).fetchone()


def safety_case_types(conn) -> list[str]:
    row = _row(conn, "close_safety_case")
    return list((row or {}).get("settings", {}).get("case_types") or SAFETY_CASE_TYPES)


def require_person(conn, kind: str, actor: str, *, ref: str | None = None, item_id: int | None = None,
                   what: str | None = None) -> None:
    """A fixed gate: a named person may go on; a machine actor is refused. Call it before changing anything. The
    refusal is logged (the caller's transaction, refused, rolls back)."""
    if kind not in FIXED:
        raise ValueError(f"no fixed gate {kind}")
    if not machine(actor):
        return
    reason = f"{FIXED[kind]} needs a named person; {actor or 'an unnamed caller'} can't do it" + (
        f" ({what})" if what else "")
    log.warning("gate refused: %s", reason)
    raise GateRefused(reason)


# A bypass is in place while its state says so (the safeguards source, P3.9a); the words sites use.
BYPASS_IN_PLACE = ("active", "in place", "applied", "bypassed", "inhibited", "overridden", "forced")


def bypassed(conn, subjects, at=None) -> list[dict]:
    """The safeguards bypassed now on `subjects`: a bypass in place, on a safeguard that protects or acts on a
    subject, what is part of or installed at one, or what one is part of or installed at. Read from the bypass and
    safeguard registers; an empty list where none are loaded."""
    subjects = [s for s in subjects or [] if s]
    if not subjects:
        return []
    try:
        with conn.transaction():
            rows = conn.execute(
                """
                with near as (
                    select unnest(%(s)s::text[]) as id
                    union select src from edge where rel in ('part_of', 'installed_at') and dst = any(%(s)s)
                      and valid_to is null
                    union select dst from edge where rel in ('part_of', 'installed_at') and src = any(%(s)s)
                      and valid_to is null)
                select distinct bl.label as bypass, sl.label as safeguard,
                       (select g.value #>> '{}' from golden g where g.fuzer_id = s.fuzer_id and g.field = 'function')
                         as function,
                       st.value #>> '{}' as state, fr.value #>> '{}' as since,
                       (select g.value #>> '{}' from golden g where g.fuzer_id = b.fuzer_id and g.field = 'moc') as moc
                from entity b
                join edge l on l.src = b.fuzer_id and l.rel = 'bypasses' and l.valid_to is null
                join entity s on s.fuzer_id = l.dst and s.kind = 'safeguard'
                join edge pe on pe.src = s.fuzer_id and pe.rel in ('protects', 'acts_on') and pe.valid_to is null
                join near n on n.id = pe.dst
                left join entity_label bl on bl.fuzer_id = b.fuzer_id
                left join entity_label sl on sl.fuzer_id = s.fuzer_id
                left join golden st on st.fuzer_id = b.fuzer_id and st.field = 'state'
                left join golden fr on fr.fuzer_id = b.fuzer_id and fr.field = 'bypassed_from'
                where b.kind = 'bypass' and b.retired_at is null
                order by bl.label
                """, {"s": subjects}).fetchall()
    except Exception:  # a register without the graph's tables
        return []
    from datetime import UTC, datetime

    from . import clock

    now = at or datetime.now(UTC)

    def started(v) -> bool:
        try:
            t = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T")) if v else None
        except ValueError:
            return True
        return t is None or clock.aware(t) <= now
    return [dict(r) for r in rows if (r["state"] or "").lower() in BYPASS_IN_PLACE and started(r["since"])]


def refuse_on_bypass(conn, subjects, actor: str, *, what: str, ref: str | None = None,
                     item_id: int | None = None) -> str | None:
    """The `bypass_safeguard` fixed gate for a proposal: an agent's (or any machine's) proposal about equipment whose
    safeguard is bypassed doesn't go ahead, whatever its confidence, as it may rely on the protection that is out. A
    named person may go on. Returns the reason (recorded as a gate decision) when it is refused, else None."""
    if not machine(actor):
        return None
    out = bypassed(conn, subjects)
    if not out:
        return None
    named = "; ".join(f"{b['function'] or b['safeguard']} ({b['safeguard']}) bypassed under {b['bypass']}"
                      + (f", {b['moc']}" if b["moc"] else ", no change record") for b in out)
    reason = (f"{what} stopped for a person: {FIXED['bypass_safeguard'].lower()} is a person's call, and it may rely "
              f"on a safeguard that is out: {named}.")
    _record(conn, "bypass_safeguard", actor, fired=True, reason=reason, ref=ref, item_id=item_id)
    log.warning("gate refused: %s", reason)
    return reason


def threshold(conn, kind: str, criticality: str | None) -> float | None:
    """The threshold for this decision kind on an asset of this criticality (None: always a person)."""
    k = CONFIDENCE[kind]
    row = _row(conn, kind)
    by_crit = {**k.by_criticality, **((row or {}).get("settings", {}).get("by_criticality") or {})}
    default = (row or {}).get("settings", {}).get("default", k.default) if row else k.default
    crit = (criticality or "").strip().upper()
    return by_crit.get(crit, default)


def criticality(conn, fuzer_id: str | None) -> str | None:
    """An asset's criticality (SAP ABC indicator, in the golden record), or None when it isn't assessed."""
    if not fuzer_id:
        return None
    try:
        with conn.transaction():
            row = conn.execute("select value #>> '{}' as v from golden where fuzer_id = %s and field = 'criticality'",
                               (fuzer_id,)).fetchone()
    except Exception:  # a register without golden records
        return None
    return (row["v"] or "").strip().upper() or None if row else None


def most_critical(values) -> str | None:
    """The most critical of several assets' ABC indicators: A outranks B outranks C (so the tightest gate applies
    to a case on more than one asset). None when none is assessed."""
    return min((v for v in values if v), default=None)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def check(conn, kind: str, confidence: float | None, *, actor: str, criticality: str | None = None,
          could_not_check=(), ref: str | None = None, item_id: int | None = None) -> dict:
    """A confidence gate: may this automatic or agent decision go ahead? Records the decision either way."""
    if kind not in CONFIDENCE:
        raise ValueError(f"no confidence gate {kind}")
    t = threshold(conn, kind, criticality)
    label = CONFIDENCE[kind].label
    crit = (f" on {'an' if criticality.upper() in ('A', 'E', 'F', 'H', 'I', 'L', 'M', 'N', 'O', 'R', 'S', 'X') else 'a'} "
            f"{criticality}-criticality asset") if criticality else ""
    missed = [c for c in could_not_check if c]
    if missed:
        fired, reason = True, f"{label} stopped for a person: it could not check {', '.join(missed)}."
    elif t is ALWAYS:
        fired, reason = True, f"{label}{crit} always stops for a person."
    elif confidence is None:
        fired, reason = True, f"{label} stopped for a person: it gave no confidence (needs {_pct(t)})."
    elif confidence < t:
        fired, reason = True, (f"{label} stopped for a person: its confidence {_pct(confidence)} is below the "
                               f"{_pct(t)} needed{crit}.")
    else:
        fired, reason = False, f"{label} went ahead: its confidence {_pct(confidence)} meets the {_pct(t)} needed{crit}."
    row = _record(conn, kind, actor, fired=fired, reason=reason, confidence=confidence, threshold=t,
                  criticality=criticality, ref=ref, item_id=item_id, could_not_check=missed)
    return {"proceed": not fired, "fired": fired, "threshold": t, "reason": reason, "decision_id": row["id"]}


def policy(conn) -> dict:
    """The policy as it stands: the fixed gates, the safety case types and each confidence gate's thresholds."""
    out = []
    for name, k in CONFIDENCE.items():
        row = _row(conn, name)
        s = (row or {}).get("settings", {})
        out.append({"kind": name, "label": k.label, "default": s.get("default", k.default) if row else k.default,
                    "by_criticality": {**k.by_criticality, **(s.get("by_criticality") or {})},
                    "updated_by": (row or {}).get("updated_by"), "updated_at": (row or {}).get("updated_at")})
    return {"fixed": [{"kind": k, "label": v} for k, v in FIXED.items()], "safety_case_types": safety_case_types(conn),
            "confidence": out}


def _valid(t) -> float | None:
    if t is None:
        return None
    t = float(t)
    if not 0 < t <= 1:
        raise ValueError("a threshold is between 0 and 1 (or none: always a person)")
    return t


def set_threshold(conn, kind: str, default, by_criticality: dict | None, by: str) -> dict:
    """Change a confidence gate (a person only). A high-criticality asset can never be set to go ahead unchecked
    where the shipped policy always stops: A stays a person."""
    if kind not in CONFIDENCE:
        raise ValueError(f"no confidence gate {kind}; the fixed gates can't be changed")
    if machine(by):
        raise GateRefused(f"changing a gate needs a named person; {by} can't")
    crit = {str(c).strip().upper(): _valid(v) for c, v in (by_criticality or {}).items()}
    for c, v in CONFIDENCE[kind].by_criticality.items():
        if v is ALWAYS and crit.get(c, ALWAYS) is not ALWAYS:
            raise ValueError(f"{c}-criticality assets always stop for a person")
    settings = {"default": _valid(default), "by_criticality": crit}
    conn.execute("insert into gate_policy (kind, settings, updated_by) values (%s, %s, %s) on conflict (kind) do "
                 "update set settings = excluded.settings, updated_by = excluded.updated_by, updated_at = now()",
                 (kind, Jsonb(settings), by))
    conn.commit()
    return next(c for c in policy(conn)["confidence"] if c["kind"] == kind)


def set_safety_case_types(conn, case_types: list[str], by: str) -> list[str]:
    """Add case types to the close_safety_case gate. The shipped ones stay: the list can only grow."""
    if machine(by):
        raise GateRefused(f"changing a gate needs a named person; {by} can't")
    types = sorted({*SAFETY_CASE_TYPES, *(t.strip() for t in case_types if t and t.strip())})
    conn.execute("insert into gate_policy (kind, settings, updated_by) values ('close_safety_case', %s, %s) "
                 "on conflict (kind) do update set settings = excluded.settings, updated_by = excluded.updated_by, "
                 "updated_at = now()", (Jsonb({"case_types": types}), by))
    conn.commit()
    return types


def decisions(conn, item_id: int) -> list[dict]:
    return conn.execute("select * from gate_decision where item_id = %s order by id", (item_id,)).fetchall()

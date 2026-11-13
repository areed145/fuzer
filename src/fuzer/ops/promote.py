"""Promote confirmed anomalies to rules (P3.4, #951): when the agentic watch's proposals of one pattern are confirmed real
several times, the watch drafts a deterministic detector for the pattern, with its replay score. A person reviews the
draft and publishes it. From then on the rule catches the pattern, and the watch stops spending on it.

- `confirm(conn, proposal_id, real, by)`: a person's verdict on a proposal (on replay, `watch_eval.judge` gives it from
  the fake plant's events). A confirmed proposal is read into a **pattern**: the measurement role of the point it names,
  the equipment class of the point's unit, and which way the value moved against the week before (`high` or `low`).
  The pattern key is `<class>:<role>:<direction>`. The reading also keeps how far the value moved (in the week's
  standard deviations) and for how long.
- `patterns(conn)`: each pattern with its confirmed and rejected proposals, and the rule drafted for it, if any.
- `draft(conn, pattern, ...)`: once `watch.promote_after` proposals of a pattern are confirmed, a rule is drafted. Points
  of that role on that class, whose average over `minutes` moves past `z` of the week before's spread, in that
  direction. The threshold is 0.8 of the smallest confirmed shift, and the duration half the shortest (30 minutes at
  least), so the rule catches every example it was fitted on with some margin. The draft is scored by the scorer
  given (on a bundle, `watch_eval.rule_scorer` replays it): recall over the kinds of event its examples matched, and
  precision and false alarms a week as the replay counts them.
- `publish(conn, rule_id, by)`: a person, never an agent (D8), publishes a draft. Published rules run as the
  `promoted_rules` window detector, through the SRE scan like any other.

The watch leaves alone a stretch on equipment a detection already covers (`watch.propose`), so once a published rule
catches the pattern, nothing about it is asked and nothing is spent.
"""

import json
from datetime import datetime, timedelta

import numpy as np

from ..core import detectors, settings

BASELINE_DAYS = 7
STEP = 10  # minutes between the values a rule reads
MARGIN = 0.8  # the threshold is this share of the smallest confirmed shift
MIN_Z = 2.0  # never below this many standard deviations, whatever the examples say
MIN_MINUTES = 30
DIRECTIONS = ("high", "low")


class PromoteError(ValueError):
    pass


def _grid(a: datetime, b: datetime, step: int = STEP) -> list[datetime]:
    return [a + timedelta(minutes=step * i) for i in range(int((b - a).total_seconds() // 60 // step) + 1)]


def _point(conn, pid: str) -> dict | None:
    from ..sources.timeseries import _point

    return _point(conn, pid)


def point_of(conn, p: dict) -> str | None:
    """The historian point a proposal is about: a timeseries it cites, else the one its subject names."""
    names = [str(x) for x in [*(p.get("evidence") or []), p.get("subject")] if x]
    if not names:
        return None
    r = conn.execute(
        """
        select e.fuzer_id from entity e left join entity_label l on l.fuzer_id = e.fuzer_id
        where e.kind = 'timeseries' and (e.fuzer_id = any(%s) or l.label = any(%s))
        order by array_position(%s, e.fuzer_id) nulls last limit 1
        """, (names, names, names)).fetchone()
    return r["fuzer_id"] if r else None


def shift(conn, point_id: str, start: datetime, end: datetime | None, src=None) -> dict | None:
    """How far a point's value moved over [start, end] against the `BASELINE_DAYS` before: the largest average over
    `MIN_MINUTES` in standard deviations of the week, its direction, and how long it stayed past half of that."""
    from ..analysis import signals
    from ..sources.timeseries import provider

    p = _point(conn, point_id)
    if not p:
        return None
    src = src or provider()
    end = end if end and end > start else start + timedelta(hours=1)
    base = signals._read(src, p, start - timedelta(days=BASELINE_DAYS), start, STEP)
    now = signals._read(src, p, start, end, STEP)
    base, now = base[~np.isnan(base)], now[~np.isnan(now)]
    if len(base) < 12 or len(now) < 2:
        return None
    mu, sd = float(np.mean(base)), max(float(np.std(base)), 1e-6 * max(1.0, abs(float(np.mean(base)))))
    n = max(1, min(len(now), MIN_MINUTES // STEP))
    avg = np.convolve(now, np.ones(n) / n, mode="valid")
    z = (avg - mu) / sd
    peak = float(z[np.argmax(np.abs(z))])
    past = np.abs((now - mu) / sd) >= abs(peak) / 2
    return {"z": round(peak, 2), "direction": "high" if peak > 0 else "low", "minutes": int(past.sum()) * STEP,
            "baseline_mean": round(mu, 4), "baseline_sd": round(sd, 4)}


def _role_class(conn, point_id: str) -> tuple[str | None, str | None]:
    r = conn.execute("select role, asset from measurement_role where point_id = %s", (point_id,)).fetchone()
    if not r:
        return None, None
    from ..analysis import limits

    unit = r["asset"] or limits.units(conn, [point_id]).get(point_id)
    return r["role"], detectors.classes_of(conn, [unit]).get(unit) if unit else None


def confirm(conn, proposal_id: int, real: bool, by: str, src=None, event_kind: str | None = None) -> dict:
    """A verdict on a watch proposal. A confirmed one is read into its pattern (role, class, direction)."""
    p = conn.execute("select * from watch_proposal where id = %s", (proposal_id,)).fetchone()
    if not p:
        raise PromoteError(f"no watch proposal {proposal_id}")
    fields = {"verdict": "confirmed" if real else "rejected", "verdict_by": by, "event_kind": event_kind}
    if real:
        pid = point_of(conn, dict(p))
        role, cls = _role_class(conn, pid) if pid else (None, None)
        s = shift(conn, pid, p["start"] or p["since"], p["end"], src) if pid else None
        fields |= {"point_id": pid, "role": role, "class": cls}
        if s:
            fields |= {"direction": s["direction"], "shift_z": s["z"], "minutes": s["minutes"]}
        if role and cls and s:
            fields["pattern"] = f"{cls}:{role}:{s['direction']}"
    sets = ", ".join(f"{k} = %s" for k in fields)
    row = conn.execute(f"update watch_proposal set {sets}, verdict_at = now() where id = %s returning *",
                       [*fields.values(), proposal_id]).fetchone()
    conn.commit()
    return dict(row)


def patterns(conn) -> list[dict]:
    """Each pattern of confirmed proposals: how many confirmed, the examples, and its rule if one was drafted."""
    rows = conn.execute(
        """
        select pattern, min(role) as role, min(class) as class, min(direction) as direction,
               count(*) as confirmed, array_agg(id order by id) as proposals,
               min(abs(shift_z)) as smallest_z, min(minutes) as shortest_minutes
        from watch_proposal where verdict = 'confirmed' and pattern is not null group by pattern order by 5 desc, 1
        """).fetchall()
    rules = {r["pattern"]: dict(r) for r in conn.execute(
        "select * from promoted_rule where status in ('draft', 'published')")}
    need = int(settings.get(conn, "watch.promote_after"))
    return [{**dict(r), "needed": need, "ready": r["confirmed"] >= need, "rule": rules.get(r["pattern"])} for r in rows]


def fit(examples: list[dict]) -> dict:
    """A rule's threshold and duration from its confirmed examples (each with shift_z and minutes)."""
    zs = [abs(e["shift_z"]) for e in examples if e.get("shift_z") is not None]
    mins = [e["minutes"] for e in examples if e.get("minutes")]
    if not zs:
        raise PromoteError("no confirmed example says how far the value moved")
    return {"z": round(max(MIN_Z, MARGIN * min(zs)), 2),
            "minutes": max(MIN_MINUTES, (min(mins) // 2 // STEP) * STEP if mins else MIN_MINUTES)}


def draft(conn, pattern: str, by: str = "agent:watch", scorer=None) -> dict:
    """Draft a rule for a pattern with enough confirmed proposals, scored by `scorer(spec, kinds)` when given (on a
    bundle's replay: `watch_eval.rule_scorer`)."""
    p = next((x for x in patterns(conn) if x["pattern"] == pattern), None)
    if not p:
        raise PromoteError(f"no confirmed proposal has the pattern {pattern!r}")
    if not p["ready"]:
        raise PromoteError(f"{pattern}: {p['confirmed']} confirmed, {p['needed']} needed before a rule is drafted")
    if p["rule"]:
        raise PromoteError(f"{pattern} already has a rule ({p['rule']['status']}, #{p['rule']['id']})")
    ex = [dict(r) for r in conn.execute(
        "select id, point_id, start, \"end\", shift_z, minutes, event_kind from watch_proposal where id = any(%s)",
        (p["proposals"],))]
    spec = {"role": p["role"], "class": p["class"], "direction": p["direction"], **fit(ex),
            "baseline_days": BASELINE_DAYS, "cadence": "6h"}
    score = scorer(spec, {e["event_kind"] for e in ex if e["event_kind"]}) if scorer else None
    row = conn.execute(
        "insert into promoted_rule (pattern, spec, examples, score, drafted_by) values (%s, %s, %s, %s, %s) returning *",
        (pattern, json.dumps(spec), json.dumps(ex, default=str), json.dumps(score) if score else None, by)).fetchone()
    conn.commit()
    return dict(row)


def publish(conn, rule_id: int, by: str, reason: str | None = None) -> dict:
    """A person publishes a drafted rule: it then runs as the promoted_rules detector. Agents can't (D8)."""
    if not by or by.startswith("agent:"):
        raise PromoteError("a person publishes a rule, never an agent")
    r = conn.execute("select * from promoted_rule where id = %s", (rule_id,)).fetchone()
    if not r:
        raise PromoteError(f"no drafted rule #{rule_id}")
    if r["status"] != "draft":
        raise PromoteError(f"rule #{rule_id} is {r['status']}, not a draft")
    row = conn.execute("update promoted_rule set status = 'published', published_by = %s, published_at = now(), "
                       "reason = %s where id = %s returning *", (by, reason, rule_id)).fetchone()
    conn.commit()
    return dict(row)


def retire(conn, rule_id: int, by: str, reason: str | None = None) -> dict:
    row = conn.execute("update promoted_rule set status = 'retired', reason = coalesce(%s, reason) where id = %s "
                       "returning *", (reason, rule_id)).fetchone()
    if not row:
        raise PromoteError(f"no rule #{rule_id}")
    conn.commit()
    return dict(row)


def _targets(conn, spec: dict) -> list[tuple[dict, str | None]]:
    rows = conn.execute("select point_id, asset from measurement_role where role = %s", (spec["role"],)).fetchall()
    cls = detectors.classes_of(conn, [r["asset"] for r in rows])
    out = []
    for r in rows:
        if spec.get("class") and cls.get(r["asset"]) != spec["class"]:
            continue
        if p := _point(conn, r["point_id"]):
            out.append((p, r["asset"]))
    return out


def apply(conn, spec: dict, since: datetime, until: datetime, src=None, rule_id: int | None = None) -> list[dict]:
    """One rule over [since, until]: each point of its role on its class whose average over `minutes` moves past `z`
    standard deviations of the week before, in its direction."""
    from ..analysis import signals
    from ..sources.timeseries import provider

    src = src or provider()
    n = max(1, spec["minutes"] // STEP)
    sign = 1 if spec["direction"] == "high" else -1
    out = []
    for p, unit in _targets(conn, spec):
        try:
            base = signals._read(src, p, since - timedelta(days=spec.get("baseline_days", BASELINE_DAYS)), since, STEP)
            now = signals._read(src, p, since, until, STEP)
        except Exception:  # one unreadable point shouldn't stop the rule
            continue
        base = base[~np.isnan(base)]
        if len(base) < 12 or np.isnan(now).all() or len(now) < n:
            continue
        mu, sd = float(np.mean(base)), max(float(np.std(base)), 1e-6 * max(1.0, abs(float(np.mean(base)))))
        avg = np.convolve(np.nan_to_num(now, nan=mu), np.ones(n) / n, mode="valid")
        z = sign * (avg - mu) / sd
        over = np.flatnonzero(z >= spec["z"])
        if not len(over):
            continue
        times = _grid(since, until)
        start, end = times[over[0]], times[min(over[-1] + n - 1, len(times) - 1)]
        out.append({"point": p["point"], "point_id": p["id"], "unit_id": unit, "start": start, "end": end,
                    "key": f"rule:{rule_id}" if rule_id else "rule", "detector": "promoted_rules",
                    "magnitude": round(float(sign * z.max()), 2),
                    "evidence": {"rule": rule_id, "role": spec["role"], "class": spec.get("class"),
                                 "direction": spec["direction"], "z": spec["z"], "minutes": spec["minutes"],
                                 "baseline_mean": round(mu, 4), "baseline_sd": round(sd, 4)}})
    return out


@detectors.register("promoted_rules", title="A rule drafted from confirmed watch findings and published by a person",
                    kind=detectors.RULE, cadence="6h", raises=("upset",), window=True,
                    needs={"a published rule": "select 1 from promoted_rule where status = 'published' limit 1"},
                    params=("watch.promote_after",), eval="replay:sre:promoted_rules")
def promoted_rules(conn, pack, since, until, src) -> list[dict]:
    """Each rule a person published from a pattern of confirmed watch findings: points of one measurement role on one
    equipment class whose value moves past the rule's threshold against the week before, in its direction."""
    out = []
    for r in conn.execute("select id, spec from promoted_rule where status = 'published' order by id").fetchall():
        out += apply(conn, r["spec"], since, until, src, rule_id=r["id"])
    return out
